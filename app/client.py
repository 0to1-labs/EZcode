#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EZcode 桌面客户端 (pywebview + WebView2, 原生窗口)
桥接设计(吸取旧版gui.py的教训):
- 事件单通道: 引擎emit → 推送队列(加锁) → flusher线程批量 evaluate_js("window.ezcodeBridge.event(<json>)")
- 合并窗口60ms: token级delta批量推, 高频流不卡UI
- 权限确认: 引擎等待Event, 前端回选经js_api, 全程字符串
- 启动自检: 依赖缺失/配置缺失给出明确指引, 不白屏
运行: python -X utf8 client.py
"""
import os
import sys
import json
import time
import socket
import threading

if getattr(sys, "frozen", False):
    # PyInstaller 会把模块摊平到 _MEIPASS 根目录, __file__ 不再位于 app/ 子目录,
    # 因此显式按打包布局解析, 保证 config/prompts/logs/app/webui 全部命中。
    ROOT = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    BASE_DIR = os.path.join(ROOT, "app")
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, ROOT)

for _s in (sys.stdout, sys.stderr):
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _warn(text):
    """无控制台(pythonw)时 sys.stdout/stderr 可能是 None, 输出必须容错。"""
    s = getattr(sys, "stderr", None)
    if s is not None:
        try:
            s.write(str(text) + "\n")
        except Exception:
            pass


def _alert(msg):
    """启动失败时弹GUI提示(无控制台也能看见), 不再依赖 print/input。"""
    _warn(msg)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(msg), "EZcode · 启动失败", 0x10)
    except Exception:
        pass


# ---- 启动计时(可选) ----
# 仅当 SECOP_BOOT_PROFILE 指向一个文件路径时写各阶段耗时, 正常运行零开销。
_T0 = time.perf_counter()
_PROF_PATH = os.environ.get("SECOP_BOOT_PROFILE")


def _mark(label):
    if not _PROF_PATH:
        return
    try:
        d = os.path.dirname(_PROF_PATH)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(_PROF_PATH, "a", encoding="utf-8") as f:
            f.write("%.4f %s\n" % (time.perf_counter() - _T0, label))
    except Exception:
        pass


def _profile_dir():
    """固定 WebView2 用户数据目录: 复用浏览器缓存, 二次启动更快,
    且 localStorage(主题偏好)得以持久化。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "EZcode", "webview-profile")


def _free_port():
    """private_mode=False 时 pywebview 会用固定 42001 端口, 多实例必冲突;
    显式给一个随机空闲端口, 保持原先 private 模式的随机端口语义。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_mark("client_imported")


def self_check():
    """启动自检。打包版依赖由构建期保证; 源码版用 find_spec 检查,
    不再为了"看看在不在"就把 requests 整套(约 136ms)加载进启动路径。"""
    if not getattr(sys, "frozen", False):
        import importlib.util
        missing = [n for n in ("requests", "webview")
                   if importlib.util.find_spec(n) is None]
        if missing:
            _alert("缺少依赖: %s\n请在 Python 里执行: pip install %s\n"
                   "(当前解释器: %s, 版本 %s)" %
                   (", ".join(missing), " ".join(missing),
                    sys.executable, sys.version.split()[0]))
            sys.exit(1)
    if not any(os.path.exists(os.path.join(ROOT, n))
               for n in ("devices.json", "config.json")):
        _alert("缺少接入配置 devices.json\n"
               "请先复制 devices.example.json 为 devices.json 并填入凭据")
        sys.exit(1)


self_check()
_mark("self_check_done")

# webview 在 run() 里导入, engine/store 在后台线程里导入:
# 让 requests / tools / verify / 证书扫描与 WebView2 进程初始化并行,
# 主线程启动路径上只留最轻的部分。


class _JsApi:
    """pywebview 的 get_functions() 会对 js_api 的公开非 callable 属性做递归遍历
    (webview/util.py:190-206)。若把 Client 实例整体传进去, self.window 指向
    WinForms 窗口, 递归会钻进 COM 对象树, 产生上千次异常与 recursion 报错,
    实测把首屏握手拖慢约 22 秒。这里只暴露纯方法门面(全是 bound method,
    不会被递归), 状态仍由闭包持有的 Client 实例保管。"""
    _METHODS = ("js_ready", "bootstrap", "boot_done", "state", "history",
                "send", "stop", "confirm_result", "new_session", "open_session",
                "delete_session", "list_sessions", "set_mode", "set_model", "set_lang",
                "set_notify",
                "set_persona", "personas_list", "persona_create", "persona_delete",
                "llm_config", "save_llm_config", "llm_providers", "test_llm_config",
                "settings", "save_settings", "mcp_test",
                "sec_probe_list", "sec_sample", "sec_adapt", "sec_apply_mapping",
                "rag_test", "connector_status",
                "skills_list", "skills_save", "skills_delete",
                "plugins_list", "plugin_install", "plugin_set_enabled",
                "plugin_delete", "pick_plugin_file", "pick_attach_file",
                "compact_now",
                "export_session",
                "update_state", "check_update", "download_update", "install_update",
                "save_update_settings", "ignore_update", "open_update_dir")

    def __init__(self, c):
        for name in self._METHODS:
            setattr(self, name, getattr(c, name))


def _dlg_open_type():
    """open-file 对话框类型, 兼容 pywebview 5/6:
    pywebview>=6 用 FileDialog.OPEN 枚举(旧常量已删除, 直接引用会 AttributeError),
    旧版用 FILEDIALOG_OPEN_FILE 常量, 都没有时退回默认值 10(open file)。"""
    import webview
    fd = getattr(webview, "FileDialog", None)
    if fd is not None and hasattr(fd, "OPEN"):
        return fd.OPEN
    v = getattr(webview, "FILEDIALOG_OPEN_FILE", None)
    if v is not None:
        return v
    return 10


def pick_open_file(window, file_types):
    """通用原生文件选择框: 返回选中绝对路径列表, 取消返回 None/空。
    window=None 或窗口已销毁时抛异常, 由调用方兜底。"""
    return window.create_file_dialog(
        _dlg_open_type(), allow_multiple=False, file_types=tuple(file_types))


class Client:
    """pywebview js_api 桥: 前端调 pywebview.api.<方法>, 引擎事件推 window.ezcodeBridge.event()"""

    def __init__(self):
        self.window = None
        self._q = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._ready = threading.Event()          # 前端onload后置位, 之前事件缓存
        self._pend = []
        self._push_err_reported = False
        # 引擎在后台线程构造, 与 WinForms/WebView2 初始化重叠
        self.engine = None
        self._engine_ready = threading.Event()
        self._engine_err = None
        threading.Thread(target=self._flusher, daemon=True).start()
        threading.Thread(target=self._init_engine, daemon=True).start()
        # 自动更新: 启动即后台检查, 与引擎/WebView2 初始化并行, 不占首屏时间
        self._upd_lock = threading.Lock()
        self._upd_busy = False
        self._update = {"ok": True, "state": "checking", "has_update": False,
                        "sources": [], "release": None, "config": {}}
        threading.Thread(target=self._auto_update, daemon=True).start()
        self._notifier = None                    # 完成提示浮窗, 首次用到才创建
        _mark("engine_thread_started")

    # ---------- 自动更新(后台线程, 失败一律静默降级) ----------

    def _emit_update(self):
        with self._upd_lock:
            data = dict(self._update)
        self._on_event({"t": "update", "data": data})

    def _auto_update(self):
        try:
            import updater as U
            cfg = U.load_config()
            if not cfg.get("enabled") or not cfg.get("check_on_startup"):
                with self._upd_lock:
                    self._update = {"ok": True, "state": "disabled",
                                    "current": U.APP_VERSION, "has_update": False,
                                    "sources": [], "release": None,
                                    "config": U._public_config(cfg)}
                self._emit_update()
                return
            self._run_update_check(cfg)
        except Exception as e:
            with self._upd_lock:
                self._update = {"ok": False, "state": "failed", "has_update": False,
                                "error": "%s: %s" % (type(e).__name__, e),
                                "sources": [], "release": None, "config": {}}
            self._emit_update()

    def _run_update_check(self, cfg=None):
        """检查全部更新源; 结果缓存 + 事件推送, 前端随时拉取不重复请求。"""
        import updater as U
        with self._upd_lock:
            if self._upd_busy:
                return
            self._upd_busy = True
            prev = dict(self._update)
            prev["state"] = "checking"
            self._update = prev
        self._emit_update()
        try:
            r = U.check(cfg) if cfg is not None else U.check()
            with self._upd_lock:
                old_dl = self._update.get("download") or {}
                if old_dl.get("path") and os.path.isfile(old_dl["path"]):
                    r["download"] = old_dl          # 保留已下载好的安装包
                self._update = r
        except Exception as e:
            with self._upd_lock:
                self._update = {"ok": False, "state": "failed", "has_update": False,
                                "error": "%s: %s" % (type(e).__name__, e),
                                "sources": [], "release": None, "config": {}}
        finally:
            with self._upd_lock:
                self._upd_busy = False
            self._emit_update()

    def _do_download(self, release):
        import updater as U
        with self._upd_lock:
            self._update["download"] = {"state": "downloading", "percent": 0}
        self._emit_update()
        last = [0.0]

        def prog(done, total, pct):
            now = time.time()
            if pct < 100 and now - last[0] < 0.25:
                return                              # 进度事件节流, 不刷爆 UI
            last[0] = now
            with self._upd_lock:
                self._update["download"] = {"state": "downloading", "percent": pct,
                                            "done": done, "total": total}
            self._emit_update()

        r = U.download(release, progress=prog)
        d = {"state": "ready" if r.get("ok") else "failed", "percent": 100 if r.get("ok") else 0,
             "path": r.get("path", ""), "size": r.get("size", 0),
             "verified": r.get("verified", False), "error": r.get("error", "")}
        with self._upd_lock:
            self._update["download"] = d
        self._emit_update()

    def update_state(self):
        import updater as U
        with self._upd_lock:
            d = dict(self._update)
        d["app_version"] = U.APP_VERSION
        d["app_name"] = U.APP_NAME
        d["download_dir"] = U.download_dir()
        d["busy"] = self._upd_busy
        return d

    def check_update(self):
        with self._upd_lock:
            if self._upd_busy:
                return {"ok": False, "error": "正在检查更新, 请稍候"}
        threading.Thread(target=self._run_update_check, daemon=True).start()
        return {"ok": True, "checking": True}

    def download_update(self):
        with self._upd_lock:
            if self._upd_busy:
                return {"ok": False, "error": "正在检查更新, 请稍候"}
            rel = (self._update.get("release") or {})
        if not (rel.get("package") or {}).get("url"):
            return {"ok": False, "error": "没有可用的更新包"}
        threading.Thread(target=self._do_download, args=(rel,), daemon=True).start()
        return {"ok": True, "downloading": True}

    def install_update(self):
        import updater as U
        with self._upd_lock:
            d = dict(self._update.get("download") or {})
        path = d.get("path") or ""
        if not path or not os.path.isfile(path):
            return {"ok": False, "error": "安装包尚未下载完成"}
        r = U.install(path, launch=True)
        if not r.get("ok"):
            return self._norm(r)

        def _quit():
            time.sleep(0.5)                        # 让本次 js_api 调用先返回前端
            try:
                if self.window:
                    self.window.destroy()
            except Exception:
                os._exit(0)
        threading.Thread(target=_quit, daemon=True).start()
        return {"ok": True, "exiting": True, "note": r.get("note", "")}

    def save_update_settings(self, update):
        import updater as U
        r = U.save_config(update or {})
        if r.get("ok"):
            threading.Thread(target=self._run_update_check, daemon=True).start()
        return self._norm(r)

    def ignore_update(self, version):
        import updater as U
        r = U.ignore_version(str(version or ""))
        if r.get("ok"):
            threading.Thread(target=self._run_update_check, daemon=True).start()
        return self._norm(r)

    def open_update_dir(self):
        import updater as U
        try:
            os.startfile(U.download_dir())
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    # ---------- 引擎惰性初始化 ----------

    def _init_engine(self):
        _mark("engine_import_start")
        try:
            import engine as E                    # noqa: E402
            self.engine = E.Engine(emit=self._on_event)
            _mark("engine_ready")
        except Exception as e:
            self._engine_err = "%s: %s" % (type(e).__name__, e)
            _warn("[boot] 研判引擎初始化失败: %s" % self._engine_err)
            _mark("engine_failed")
        finally:
            self._engine_ready.set()

    def _engine(self):
        """js_api 统一入口: 引擎未就绪时短暂等待, 不把启动阻塞在主线程。"""
        if self.engine is None:
            self._engine_ready.wait(10)
        if self.engine is None:
            raise RuntimeError(self._engine_err or "研判引擎尚未就绪")
        return self.engine

    # ---------- 引擎事件 → 前端(批量合并) ----------

    def _on_event(self, payload):
        with self._lock:
            if not self._ready.is_set():
                self._pend.append(payload)       # 前端没好之前只缓存
                return
            self._q.append(payload)
        self._wake.set()

    def _flusher(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            time.sleep(0.06)                     # 合并窗口
            with self._lock:
                batch, self._q = self._q, []
            if not batch:
                continue
            for payload in batch:
                self._push_one(payload)

    def _push_one(self, payload):
        try:
            self.window.evaluate_js("window.ezcodeBridge.event(%s)"
                                    % json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            # 只报一次避免刷屏; 静默吞异常会让"界面完全无反应"类故障极难排查
            if not self._push_err_reported:
                self._push_err_reported = True
                _warn("[bridge] 事件推送失败: %s: %s" % (type(e).__name__, e))
        if payload.get("t") == "round_end" and not payload.get("cancelled"):
            self._notify_done(payload)

    # ---------- 任务完成提示 ----------

    def _notifier_obj(self):
        if self._notifier is None:
            import notify as N
            self._notifier = N.Notifier(self)
        return self._notifier

    def _notify_done(self, p):
        """一轮回答结束 → 右下角浮窗。只在你没盯着这个任务时出现(auto)。"""
        try:
            eng = self.engine
            if eng is None:
                return
            ui = (eng.cfg.get("ui") or {})
            mode = str(ui.get("notify") or "auto")
            n = self._notifier_obj()
            if not n.allowed(mode, p.get("sid")):
                return
            title = (p.get("title") or "").strip() or (p.get("persona_label") or "").strip() or "任务已完成"
            body = (p.get("answer") or "").strip() or "回答已完成，点击回到这个任务"
            n.notify(sid=p.get("sid") or "", title=title, body=body,
                     icon=p.get("persona_icon") or "✅",
                     dt=p.get("dt") or 0, tools=p.get("n_tools") or 0)
        except Exception as e:
            _warn("[notify] 完成提示失败: %s: %s" % (type(e).__name__, e))

    def set_ready(self):                          # 前端onload调 js_ready
        with self._lock:
            # 缓存事件插到队首, 由flusher统一按序推, 避免与新事件乱序
            self._q = self._pend + self._q
            self._pend = []
            has_pend = bool(self._q)
        self._ready.set()
        if has_pend:
            self._wake.set()

    # ---------- js_api: 前端调用 ----------

    @staticmethod
    def _norm(r):
        """统一返回契约: 错误分支也带 ok:false, 前端用 r.ok / r.error 都安全。"""
        if isinstance(r, dict) and "error" in r:
            return dict(r, ok=False)
        return r

    def js_ready(self):
        self.set_ready()
        return {"ok": True}

    def boot_done(self):
        """前端渲染完成后回报, 用于确认首屏真正可操作(不只是 Python 端握手)。"""
        _mark("frontend_rendered")
        return {"ok": True}

    def bootstrap(self):
        """一次往返完成启动握手: 置ready + 状态 + 历史 + 会话列表。
        pywebview 的 js_api 每次调用都有COM往返开销, 分开调会让首屏慢数秒
        且任一环失败就卡住; 合并后前端只需等一个Promise。"""
        _mark("bootstrap_call")
        try:
            eng = self._engine()
        except Exception as e:
            # 引擎仍在后台初始化: 明确返回失败让前端轮询重试, 不给半截数据
            return {"ok": False, "error": str(e)}
        self.set_ready()
        d = {"ok": True, "state": eng.state(),
             "history": eng.history(),
             "sessions": eng.list_sessions(),
             "update": self.update_state()}   # 更新状态随握手一次带回, 省一次COM往返
        _mark("bootstrap_done")
        return d

    def state(self):
        return self._engine().state()

    def history(self):
        return self._engine().history()

    def send(self, text):
        return self._norm(self._engine().send(text))

    def stop(self, sid=None):
        """不传=停止当前任务; 传 sid=停止后台某个指定任务(任务列表的■按钮)。"""
        return self._norm(self._engine().stop(sid))

    def confirm_result(self, seq, choice):
        return self._norm(self._engine().confirm_result(seq, choice))

    def new_session(self):
        r = self._engine().new_session()
        return self._norm(r) or {"ok": True}

    def open_session(self, sid):
        r = self._engine().open_session(str(sid))
        return self._norm(r) or {"ok": True}

    def delete_session(self, sid):
        return self._norm(self._engine().delete_session(str(sid)))

    def list_sessions(self):
        return self._engine().list_sessions()

    def set_mode(self, mode):
        r = self._engine().set_mode(str(mode))
        return self._norm(r) or {"ok": True}

    def set_model(self, key):
        return self._norm(self._engine().set_model(str(key)))

    def set_lang(self, lang):
        """界面/回复语言切换(zh|en): 引擎重建系统提示词并写入 devices.json 的 ui.lang。"""
        return self._norm(self._engine().set_lang(str(lang or "zh")))

    def set_notify(self, mode):
        """任务完成桌面提示策略: auto(仅不在看该任务时) / always / off。"""
        return self._norm(self._engine().set_notify(str(mode or "")))

    def set_persona(self, persona):
        """切换专家: 内置(安全运营/通用编码)或自建专家, 下一轮生效。"""
        return self._norm(self._engine().set_persona(str(persona)))

    def personas_list(self):
        """专家中心: 专家清单(内置在前, 自建在后)。"""
        return self._norm(self._engine().personas_list())

    def persona_create(self, label, desc, prompt, icon="", tools="general", welcome=""):
        """专家中心: 新建自建专家(agents/personas/*.md, 热注册)。"""
        return self._norm(self._engine().persona_create(
            str(label or ""), str(desc or ""), str(prompt or ""),
            str(icon or ""), str(tools or "general"), str(welcome or "")))

    def persona_delete(self, pid):
        """专家中心: 删除自建专家(备份到 logs/deleted-personas/)。"""
        return self._norm(self._engine().persona_delete(str(pid or "")))

    def llm_config(self):
        """设置界面: 读当前大模型配置(完整profiles)。"""
        return self._norm(self._engine().llm_config())

    def save_llm_config(self, active, profiles):
        """设置界面: 保存大模型配置(写回配置文件+热生效)。"""
        return self._norm(self._engine().save_llm_config(active, profiles or {}))

    def llm_providers(self):
        """设置界面: 内置厂商预设(选厂商只需填 Key)。"""
        try:
            import providers as P
            return {"ok": True, "providers": P.list_providers()}
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e), "providers": []}

    def test_llm_config(self, profile):
        """设置界面: 测试一份模型配置能否连通(不落盘)。"""
        return self._norm(self._engine().test_llm_profile(profile or {}))

    def settings(self):
        """设置中心: 读全部可编辑节(大模型/系统API/情报/RAG/MCP/技能/插件)。"""
        return self._norm(self._engine().settings())

    def save_settings(self, sections):
        """设置中心: 按节保存配置并热生效(设备凭据/RAG/自定义API/MCP热重连)。"""
        return self._norm(self._engine().save_settings(sections or {}))

    def mcp_test(self, spec):
        """设置中心: 测试一台MCP服务器连通性(不落盘)。"""
        return self._norm(self._engine().mcp_test(spec or {}))

    def sec_probe_list(self):
        """设置中心: 逐设备自检(token 权限/可达性/能拉到多少条告警)。"""
        return self._norm(self._engine().sec_probe_list())

    def rag_test(self, cfg=None):
        """设置中心: 用当前表单里的 RAG 配置试检索一次(不落盘)。"""
        return self._norm(self._engine().rag_test(cfg or {}))

    def connector_status(self):
        """设置中心「连接器」: 各能力域状态(Office 检测 + 计数)。"""
        return self._norm(self._engine().connector_status())

    def sec_sample(self, project=None, device=None):
        """设置中心: 取设备响应样本(脱敏)+结构摘要, 供自动适配核对。"""
        return self._norm(self._engine().sec_sample(project, device))

    def sec_adapt(self, project=None, device=None):
        """设置中心: 自动适配(取样→模型生成映射→真实样本校验), 返回草案不落盘。"""
        return self._norm(self._engine().sec_adapt(project, device))

    def sec_apply_mapping(self, project, device, mapping):
        """设置中心: 保存字段映射到项目设备组并热生效。"""
        return self._norm(self._engine().sec_apply_mapping(project, device, mapping or {}))

    def skills_list(self):
        """设置中心: 全局+项目级技能清单。"""
        return self._norm(self._engine().skills_list())

    def skills_save(self, scope, filename, name, description, enabled, content):
        """设置中心: 新建/覆盖技能文件(保存后下一轮生效)。"""
        return self._norm(self._engine().skills_save(
            str(scope or "global"), str(filename or ""), str(name or ""),
            str(description or ""), bool(enabled), str(content or "")))

    def skills_delete(self, scope, filename):
        """设置中心: 删除技能文件。"""
        return self._norm(self._engine().skills_delete(str(scope or "global"), str(filename or "")))

    # ---------- 插件管理(设置中心「插件」分区) ----------

    def plugins_list(self):
        """列出已安装插件(启用状态/注册的工具/警告)。"""
        return self._norm(self._engine().plugins_list())

    def plugin_install(self, path=None, filename=None, code=None):
        """安装插件: path=从文件复制; filename+code=粘贴代码。装完热加载。"""
        return self._norm(self._engine().plugin_install(path=path, filename=filename, code=code))

    def plugin_set_enabled(self, filename, enabled):
        """启用/禁用插件(写回plugins.disabled, 热生效)。"""
        return self._norm(self._engine().plugin_set_enabled(str(filename or ""), bool(enabled)))

    def plugin_delete(self, filename):
        """卸载插件(删文件+注销工具)。"""
        return self._norm(self._engine().plugin_delete(str(filename or "")))

    def pick_plugin_file(self):
        """原生文件选择框(.py), 返回选中路径(不落盘)。"""
        try:
            r = pick_open_file(self.window, ("Python 插件 (*.py)",)) if self.window else None
            if not r:
                return {"ok": False, "error": "未选择"}
            return {"ok": True, "path": str(r[0])}
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

    def pick_attach_file(self):
        """输入框「＋」附加文件证据: 原生文件框返回绝对路径(不落盘、不读取)。"""
        try:
            r = pick_open_file(self.window, (
                    "文本文件 (*.txt;*.log;*.json;*.csv;*.md;*.py)",
                    "所有文件 (*.*)")) if self.window else None
            if not r:
                return {"ok": False, "error": "未选择"}
            return {"ok": True, "path": str(r[0])}
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

    def compact_now(self):
        """手动压缩上下文: 历史→LLM纪要+保留最近对话。"""
        return self._norm(self._engine().compact_now())

    def export_session(self):
        """导出markdown(含工具调用证据链)。"""
        import datetime
        path = os.path.join(ROOT, "logs", "export-%s.md"
                            % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        lines = ["# EZcode 会话导出", ""]
        for m in self._engine().sess["messages"]:
            role = m.get("role")
            c = str(m.get("content") or "")
            if role == "user":
                lines.append("## 你\n\n%s\n" % c)
            elif role == "assistant":
                for tc in (m.get("tool_calls") or []):
                    fn = tc.get("function") or {}
                    lines.append("> ⚙ **%s**(%s)\n"
                                 % (fn.get("name"), (fn.get("arguments") or "")[:300]))
                if c:
                    lines.append("## EZcode\n\n%s\n" % c)
            elif role == "tool":
                lines.append("<details><summary>⚙ 工具结果</summary>\n\n```\n%s\n```\n\n</details>\n"
                             % c[:2500])
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        try:
            os.startfile(os.path.dirname(path))
        except Exception:
            pass
        return path

    # ---------- 生命周期 ----------

    def on_closed(self):
        try:
            if self._notifier is not None:
                self._notifier.close_all()      # 别把提示窗留在桌面上
        except Exception:
            pass
        try:
            eng = self.engine            # 可能仍在后台初始化, 关闭时不等待
            if eng is None:
                return
            eng.stop()
            eng._save()
            eng.slog.close()
        except Exception:
            pass

    def run(self):
        import webview                                   # noqa: E402
        _mark("webview_imported")
        self.window = webview.create_window(
            "EZcode",
            os.path.join(BASE_DIR, "webui", "index.html"),
            js_api=_JsApi(self), width=1280, height=860, min_size=(960, 640),
            text_select=True)
        _mark("window_created")
        self.window.events.closed += self.on_closed
        try:
            os.makedirs(_profile_dir(), exist_ok=True)
            storage = _profile_dir()
        except Exception:
            storage = None
        _mark("before_webview_start")
        # private_mode=False + 固定 UserDataFolder: 不再每次启动新建临时
        # WebView2 profile(冷启动最贵的一段), 顺带让主题等 localStorage 持久化。
        # SECOP_PRIVATE_MODE=1 可切回旧行为用于对照/排障。
        private = os.environ.get("SECOP_PRIVATE_MODE") == "1"
        webview.start(debug=False, private_mode=private,
                      storage_path=(None if private else storage),
                      http_port=_free_port())


if __name__ == "__main__":
    Client().run()
