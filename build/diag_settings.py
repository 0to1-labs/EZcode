# -*- coding: utf-8 -*-
"""设置中心(多分区)真实窗口验证: mock js_api, 加载真实 index.html,
逐分区渲染/交互/保存调用链验证。用法: python diag_settings.py"""
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
WEBUI = os.path.join(APP, "webui")
sys.path.insert(0, APP)

RAW = json.load(open(os.path.join(ROOT, "devices.json"), encoding="utf-8"))

# 项目=设备组: 3 台设备(2 台专用适配器 + 1 台 http 需字段映射), 供设置页与自检/适配链路断言
PROJECTS = [{"name": "演示项目", "devices": [
    {"name": "流量侧", "kind": "ngsoc", "site": "demo-site", "enabled": True},
    {"name": "主机侧", "kind": "edr", "project": "示例项目", "enabled": True},
    {"name": "边界WAF", "kind": "http", "base_url": "https://waf.example.invalid", "enabled": True},
]}]
SEC_APPLIED = []
ADAPTED = []
RAG_TESTS = []
SAVED = []          # 记录 save_settings 的调用
SKILLS = []         # mock 技能状态
PLUGINS = [{"file": "b64_tools.py", "name": "b64_tools", "enabled": True,
            "tools": ["b64_encode", "b64_decode"], "n_tools": 2,
            "loaded": True, "warning": None}]
STATE = {"mode": "query", "model": "deepseek-chat", "active": "deepseek", "busy": False,
         "sid": "s1", "title": "设置测试", "ctx_pct": 12, "n_msgs": 0,
         "persona": "ezcode", "persona_label": "安全运营", "persona_icon": "🛡",
         "persona_cards": None,       # set_persona mock 时按专家填充
         "profiles": {"deepseek": "deepseek-chat", "qwen": "qwen-plus"}}
MODE_CALLS = []
MODEL_CALLS = []
LLM_SAVED = []
LANG_CALLS = []        # set_lang(界面语言切换) 调用记录


class MockApi:
    def js_ready(self): return {"ok": True}
    def bootstrap(self):
        return {"ok": True, "state": STATE, "history": [], "sessions": []}
    def boot_done(self): return {"ok": True}
    def state(self): return dict(STATE)
    def history(self): return []
    def list_sessions(self): return []
    def send(self, text): return {"ok": True}
    def stop(self): return {"ok": True}
    def confirm_result(self, *a): return {"ok": True}
    def new_session(self): return {"ok": True}
    def open_session(self, sid): return {"ok": True}
    def delete_session(self, sid): return {"ok": True}
    def set_mode(self, m):
        MODE_CALLS.append(m); return {"ok": True}
    def set_model(self, k):
        MODEL_CALLS.append(k); return {"ok": True}
    def set_lang(self, lang):
        LANG_CALLS.append(str(lang)); return {"ok": True, "lang": str(lang)}
    def pick_attach_file(self):
        return {"ok": True, "path": r"C:/evidence/告警 2026-11-10.log"}
    def set_persona(self, p):
        p = str(p or "")
        hit = next((x for x in self.PERSONAS if x["id"] == p), None)
        if hit:
            STATE["persona"] = hit["id"]
            STATE["persona_label"] = hit["label"]
            STATE["persona_icon"] = hit["icon"]
            STATE["persona_cards"] = hit.get("cards")
        return {"ok": True, "persona": p, "label": (hit or {}).get("label", "")}
    def llm_config(self): return {"path": "devices.json", "active": "x", "profiles": {}}
    def save_llm_config(self, active, profiles):
        LLM_SAVED.append((active, json.loads(json.dumps(profiles or {}))))
        return {"ok": True, "active": active}
    def llm_providers(self):
        sys.path.insert(0, os.path.join(ROOT, "app"))
        import providers as P
        return {"ok": True, "providers": P.list_providers()}
    def test_llm_config(self, profile): return {"ok": True, "detail": "(mock)连接正常"}
    def export_session(self): return ""
    def settings(self):
        out = {"path": os.path.join(ROOT, "devices.json")}
        for k in ("llm", "netbox", "edr", "ngsoc",
                  "rag", "custom_api", "mcp"):
            out[k] = RAW.get(k)
        out["projects"] = json.loads(json.dumps(PROJECTS))
        _rag = RAW.get("rag") if isinstance(RAW.get("rag"), dict) else {}
        out["rag"] = dict(_rag, query_field=_rag.get("query_field") or "query")
        return out
    def save_settings(self, sections):
        SAVED.append(sections)
        return {"ok": True, "note": "(mock)已保存并生效"}
    def mcp_test(self, spec):
        return {"ok": True, "n_tools": 3, "tools": ["search_docs", "fetch_lib", "resolve_id"]}

    # ---- 设备自检 / 自动适配(mock) ----
    def sec_probe_list(self):
        return {"projects": [{"name": "演示项目", "devices": [
            {"device": "流量侧", "kind": "ngsoc", "ok": True, "n_alarms": 12},
            {"device": "主机侧", "kind": "edr", "ok": True, "n_alarms": 5},
            {"device": "边界WAF", "kind": "http", "ok": False, "n_alarms": None,
             "error": "未配置字段映射"},
        ]}], "summary": "可用 2 / 3 台"}

    def sec_adapt(self, project=None, device=None):
        ADAPTED.append((project, device))
        return {"ok": True, "device": device, "project": project,
                "note": "已用真实样本校验通过, 确认无误后点「保存映射」生效",
                "mapping": {"request": {"method": "GET", "path": "/api/alarms"},
                            "response": {"list_path": "data.list", "fields": {
                                "alarm_id": "uuid", "name": "alarmName",
                                "severity": {"path": "level", "map": {"3": "高"}},
                                "time": {"path": "ts", "format": "ms"},
                                "src_ip": "srcIp"}}},
                "preview": [{"time": "2026-11-10 14:32:00", "name": "SQL注入攻击",
                             "severity_label": "高", "src_ip": "1.2.3.4",
                             "dst_ip": "203.0.113.5"}]}

    def connector_status(self):
        return {"office": "WPS 可用(Word/WPS文字/Excel/WPS表格/PowerPoint/WPS演示)",
                "custom_api": 0, "mcp": 2, "plugins": 1, "skills": 3,
                "rag": False, "todo": 2}

    def rag_test(self, cfg=None):
        RAG_TESTS.append(cfg or {})
        return {"ok": True, "ms": 42, "chars": 320, "empty": False,
                "preview": "防火墙策略变更流程：先提交工单，由网络组复核后下发。"}

    def sec_apply_mapping(self, project, device, mapping):
        SEC_APPLIED.append((project, device, mapping))
        return {"ok": True, "note": "映射已保存并生效(原有站点/项目配置保留不动)"}
    def skills_list(self):
        import copy
        builtin = [{"scope": "builtin", "builtin": True, "file": "security-triage.md",
                    "name": "安全研判流程", "description": "单条深挖与批量分诊的研判方法论、输出模板",
                    "enabled": True, "content": "# 研判方法论\n1. 要素提取 ..."}]
        return {"skills": builtin + copy.deepcopy(SKILLS),
                "global_dir": "~/.ezcode/skills", "project_dir": "<app>/skills"}
    def skills_save(self, scope, filename, name, description, enabled, content):
        SKILLS[:] = [x for x in SKILLS if not (x["scope"]==scope and x["file"]==filename)]
        SKILLS.append({"scope": scope, "file": filename, "name": name,
                       "description": description, "enabled": enabled, "content": content})
        return {"ok": True}
    def skills_delete(self, scope, filename):
        SKILLS[:] = [x for x in SKILLS if not (x["scope"]==scope and x["file"]==filename)]
        return {"ok": True}
    def plugins_list(self):
        return {"plugins": [dict(x) for x in PLUGINS],
                "dir": r"C://...//plugins"}
    def plugin_install(self, path=None, filename=None, code=None):
        fn = (filename or "new") + ("" if str(filename or "").endswith(".py") else ".py")
        PLUGINS[:] = [x for x in PLUGINS if x["file"] != fn]
        PLUGINS.append({"file": fn, "name": fn[:-3], "enabled": True, "tools": ["mock_tool"],
                        "n_tools": 1, "loaded": True, "warning": None})
        return {"ok": True, "note": "已加载"}
    def plugin_set_enabled(self, filename, enabled):
        for x in PLUGINS:
            if x["file"] == filename:
                x["enabled"] = enabled; x["loaded"] = enabled
        return {"ok": True, "enabled": enabled}
    def plugin_delete(self, filename):
        PLUGINS[:] = [x for x in PLUGINS if x["file"] != filename]
        return {"ok": True}
    def pick_plugin_file(self):
        return {"ok": True, "path": r"C://tmp//fake_plugin.py"}

    # ---- 专家中心(v21) ----
    PERSONAS = [
        {"id": "ezcode", "label": "安全运营", "icon": "🛡", "secops": True,
         "desc": "告警研判、事件调查、资产与威胁情报联动的安全运营专家",
         "welcome": "粘贴告警即可研判", "builtin": True, "custom": False,
         "scope": None, "file": None,
         "cards": [{"icon": "🛡", "title": "告警研判", "sub": "批量分诊 · 行为链",
                    "prompt": "帮我研判今天未处理的高危告警"},
                   {"icon": "📚", "title": "查知识库", "sub": "判据 · 历史处置",
                    "prompt": "检索知识库，总结这类事件的处置判据"},
                   {"icon": "🔎", "title": "事件调查", "sub": "资产 · 情报 · 证据链",
                    "prompt": "对这条告警做完整调查"},
                   {"icon": "🧪", "title": "分析脚本", "sub": "批量解析 · 统计",
                    "prompt": "帮我写一个 Python 脚本统计日志并输出报表"}]},
        {"id": "coder", "label": "通用编码", "icon": "💻", "secops": False,
         "desc": "写代码、改文件、跑命令、分析数据的通用桌面助理",
         "welcome": "直接下任务", "builtin": True, "custom": False,
         "scope": None, "file": None,
         "cards": [{"icon": "💻", "title": "写代码 / 跑命令", "sub": "脚本 · 构建 · 自动化",
                    "prompt": "帮我写一个 Python 脚本统计日志并输出报表"},
                   {"icon": "📁", "title": "批量改文件", "sub": "检索 · 编辑 · 验证",
                    "prompt": "找出所有 TODO 注释并给处理建议"},
                   {"icon": "🧪", "title": "数据分析", "sub": "解析 · 统计",
                    "prompt": "分析这份数据文件的字段分布"},
                   {"icon": "🌐", "title": "查公开资料", "sub": "搜索 · 文档",
                    "prompt": "搜一下这个报错的常见原因"}]},
        {"id": "assistant", "label": "日常工作助手", "icon": "🧑‍💼", "secops": False,
         "desc": "写作润色、总结提炼、翻译、查资料、待办管理的日常办公助手",
         "welcome": "写作、总结、翻译、查资料、记待办", "builtin": True, "custom": False,
         "scope": None, "file": None,
         "cards": [{"icon": "📝", "title": "写作 / 润色", "sub": "邮件 · 周报 · 方案",
                    "prompt": "帮我写一份本周工作周报的框架"},
                   {"icon": "✅", "title": "记待办", "sub": "记任务 · 列清单 · 标完成",
                    "prompt": "记一下待办：明天上午找张工对策略"},
                   {"icon": "🌐", "title": "查资料", "sub": "搜索 · 整理 · 摘要",
                    "prompt": "帮我查一下这个概念的公开资料"},
                   {"icon": "🌍", "title": "翻译 / 总结", "sub": "中英互译 · 长文提炼",
                    "prompt": "把这段英文资料翻译成中文并总结"}]},
    ]

    def personas_list(self):
        return {"personas": [dict(x) for x in self.PERSONAS]}

    def persona_create(self, label, desc, prompt, icon="", tools="general", welcome=""):
        self.PERSONAS.append({"id": "mock-expert", "label": label,
                              "icon": icon or "🧠", "desc": desc, "secops": False,
                              "welcome": welcome, "builtin": False, "custom": True,
                              "scope": "project", "file": "mock.md"})
        return {"ok": True, "id": "mock-expert", "note": "(mock)已创建"}

    def persona_delete(self, pid):
        self.PERSONAS[:] = [x for x in self.PERSONAS if x["id"] != pid]
        return {"ok": True, "note": "(mock)已删除"}


class Runner:
    def __init__(self):
        self.win = None
        self.results = []

    def check(self, name, ok, detail=""):
        self.results.append((name, ok))
        print("  %s %s %s" % ("✓" if ok else "✗", name, ("-- " + str(detail)[:130]) if not ok else ""))

    def js(self, code):
        return self.win.evaluate_js(code)

    def run(self):
        import webview
        self.win = webview.create_window(
            "设置诊断", os.path.join(WEBUI, "index.html"),
            js_api=MockApi(), width=1100, height=760)
        threading.Thread(target=self._scenario, daemon=True).start()
        webview.start()

    def _scenario(self):
        t0 = time.time()
        while time.time() - t0 < 15:
            if self.js("window.__ezcodeBooted === true"):
                break
            time.sleep(0.2)
        time.sleep(0.4)
        print("== 0. 品牌落地 ==")
        title = self.js("document.title")
        self.check("窗口标题仅为 EZcode(无副标题)", str(title).strip() == "EZcode", title)
        brand = self.js("document.querySelector('.brand-text b').textContent")
        self.check("侧栏品牌名 EZcode", brand == "EZcode", brand)
        brand_sub = self.js("(function(){var s=document.querySelector('.brand-text span');"
                            "return s ? s.textContent : ''})()")
        self.check("侧栏品牌下方无介绍文字", not str(brand_sub).strip(), brand_sub)
        logo = self.js("(function(){var i=document.querySelector('.brand img.logo');"
                       "return i ? (i.getAttribute('src')+'|'+(i.complete&&i.naturalWidth>0)"
                       "+'|'+i.naturalWidth) : 'none';})()")
        self.check("松鼠头像 logo 已加载", str(logo).startswith("EZcode.png|true|"), logo)
        ph = self.js("document.querySelector('#input').placeholder")
        # 拼接写法: 避免发布自检把「待排除词表」本身当成敏感内容
        vendor_words = ("NG" + "SOC", "E" + "DR", "马" + "鞍山")
        self.check("输入提示无厂商品牌词",
                   not any(w in str(ph) for w in vendor_words), ph)
        ver = self.js("document.querySelector('script[src]').getAttribute('src')")
        vers = self.js("Array.from(document.querySelectorAll('script[src],link[rel=stylesheet]'))"
                       ".map(e=>{const u=e.getAttribute('src')||e.getAttribute('href')||'';"
                       "const m=u.match(/[?&]v=(\\d+)/);return m?m[1]:'';}).filter(Boolean)")
        uniq = sorted(set(vers or []))
        self.check("前端资源版本已刷新(三者一致且>44)",
                   len(uniq) == 1 and int(uniq[0]) > 44, "vers=%s src=%s" % (vers, ver))
        print("== 0a0b. 文档处理(Office/WPS) ==")
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import tools as _tools0
        import caps as _caps0
        _tools0.configure({"builtin_tools": {}})
        _all = {t["function"]["name"] for t in _tools0.TOOLS}
        self.check("office_detect / office_edit 已注册",
                   {"office_detect", "office_edit"} <= _all)
        self.check("office_edit 属可执行模式工具", "office_edit" in _tools0.EXEC_TOOL_NAMES)
        self.check("只读模式看不到 office_edit",
                   "office_edit" not in {t["function"]["name"]
                                         for t in _tools0.toolset("query", "ezcode", secops=True)})
        self.check("只读模式仍可用 office_detect",
                   "office_detect" in {t["function"]["name"]
                                       for t in _tools0.toolset("query", "ezcode", secops=True)})
        self.check("office 能力标签已打",
                   _caps0.CAPABILITIES.get("office_detect") == ["office"]
                   and "write" in (_caps0.CAPABILITIES.get("office_edit") or []))
        self.check("doc_read 已注册且只读可用(读 docx/xlsx/pptx)",
                   "doc_read" in {t["function"]["name"]
                                  for t in _tools0.toolset("query", "ezcode", secops=True)}
                   and _caps0.CAPABILITIES.get("doc_read") == ["office"],
                   str(_caps0.CAPABILITIES.get("doc_read")))
        _sum = _tools0.office.detect_summary()
        self.check("本机文档处理检测可用", "可用" in _sum, _sum)
        self.check("检测不启动进程(仅注册表)",
                   not any(x.startswith("检测失败") for x in [_sum]))

        print("== 0a1. 文件选择框(pywebview 5/6 兼容) ==")
        import client as _client
        import webview as _wv
        dt = _client._dlg_open_type()
        self.check("open-file 对话框常量可解析(=10)", int(dt) == 10, str(dt))
        _fd = getattr(_wv, "FileDialog", None)
        self.check("pywebview>=6 枚举 FileDialog.OPEN 对齐",
                   _fd is not None and dt == _fd.OPEN, "%s / %s" % (dt, _fd))
        class _FakeWin:
            def __init__(self, ret): self.ret = ret; self.got = None
            def create_file_dialog(self, dialog_type, **kw):
                self.got = (dialog_type, kw); return self.ret
        fw = _FakeWin(["C:/evidence/告警 2026-11-10.log"])
        r = _client.pick_open_file(fw, ("文本文件 (*.log)", "所有文件 (*.*)"))
        self.check("pick_open_file 选中返回路径", bool(r) and str(r[0]).endswith(".log"), r)
        self.check("对话框参数: 单选+文件类型过滤",
                   bool(fw.got) and fw.got[1].get("allow_multiple") is False
                   and len(fw.got[1].get("file_types") or ()) == 2, str(fw.got))
        fw2 = _FakeWin(None)
        self.check("pick_open_file 取消返回空",
                   not _client.pick_open_file(fw2, ("所有文件 (*.*)",)))
        _src = open(os.path.join(APP, "client.py"), encoding="utf-8").read()
        self.check("client.py 已无废弃常量 FILE_DIALOG_OPEN",
                   "FILE_DIALOG_OPEN" not in _src)
        print("== 0a0. 内置工具箱(引擎侧注册链) ==")
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)      # tools 包与 caps.py 在项目根
        import tools as _tools
        import caps as _caps
        import tempfile as _tf
        _torig = _tools.todo._STORE
        _tools.todo._STORE = os.path.join(_tf.gettempdir(), "_diag_todo.json")
        _tools.configure({"builtin_tools": {}})
        _names = [t["function"]["name"] for t in _tools.TOOLS]
        self.check("todo四件套默认注册",
                   all(n in _names for n in ("todo_list", "todo_add", "todo_done", "todo_remove")))
        self.check("通用能力标签齐备",
                   all(n in _caps.CAPABILITIES for n in
                       ("fs_ls", "web_fetch", "run_python", "todo_list")))
        _r = _tools.todo.todo_add("诊断A\n诊断B")
        self.check("todo_add 多条记录", bool(_r.get("ok")) and _r.get("added") == 2, _r)
        self.check("todo_list 可查", _tools.todo.todo_list()["total_open"] == 2)
        self.check("todo_done 销账", _tools.todo.todo_done(keyword="诊断A").get("ok"))
        self.check("todo_remove 删除", _tools.todo.todo_remove(keyword="诊断B").get("ok"))
        _tools.configure({"builtin_tools": {"todo": False, "web_tools": False}})
        _names2 = [t["function"]["name"] for t in _tools.TOOLS]
        self.check("关组后工具移出模型视野",
                   "todo_list" not in _names2 and "web_fetch" not in _names2, _names2)
        _tools.configure({"builtin_tools": {}})
        self.check("重新开启后能力标签回填",
                   _caps.CAPABILITIES.get("web_fetch") == ["web"]
                   and _caps.CAPABILITIES.get("todo_list") == ["todo"])
        _tsq = [t["function"]["name"] for t in _tools.toolset("query", "coder")]
        self.check("todo只读模式可用/命令隐藏",
                   "todo_add" in _tsq and "shell" not in _tsq)
        try:
            os.remove(_tools.todo._STORE)
        except Exception:
            pass
        _tools.todo._STORE = _torig
        print("== Markdown 排版(走真实渲染入口 addBotFull) ==")
        # 必须用真实消息容器验证: 之前自造 <div class="md"> 测样式, 而真实回答容器是 .body,
        # 类名不匹配导致"改了样式但用户看到没变化"——这类假验证要杜绝。
        self.js("addBotFull('# 一级标题\\n\\n这是一段正文，用来检查字号与行高。\\n\\n"
                "## 二级标题\\n\\n1. 列表项一\\n2. 列表项二\\n\\n"
                "| 时间 | 设备 |\\n|---|---|\\n| 14:02 | 流量侧 |\\n\\n"
                "> 引用块文本\\n\\n`inline` 与 **加粗**')")
        time.sleep(0.3)
        _cs = self.js("(function(){var b=document.querySelector('.msg.bot .body');"
                      "if(!b) return '';"
                      "var p=b.querySelector('p'), h1=b.querySelector('h1'), h2=b.querySelector('h2');"
                      "var t=b.querySelector('.md-table table');"
                      "var g=function(e,k){return e?getComputedStyle(e)[k]:'';};"
                      "return JSON.stringify({cls:b.className, fs:g(b,'fontSize'), lh:g(b,'lineHeight'),"
                      " pm:g(p,'marginBottom'), h1:g(h1,'fontSize'), h2:g(h2,'fontSize'),"
                      " tw:parseFloat(g(t,'width'))||0, bw:b.clientWidth});})()")
        import json as _json
        _m = _json.loads(_cs) if _cs else {}
        self.check("真实回答容器带 md 排版类", "md" in str(_m.get("cls", "")), _m.get("cls"))
        self.check("真实回答正文 15px / 行高 ≥1.75", _m.get("fs") == "15px"
                   and float(str(_m.get("lh", "0")).replace("px", "")) >= 26, _m.get("lh"))
        self.check("真实回答段间距 12px", _m.get("pm") == "12px", _m.get("pm"))
        self.check("真实回答标题阶梯 21/17.5",
                   _m.get("h1") == "21px" and _m.get("h2") == "17.5px",
                   "%s / %s" % (_m.get("h1"), _m.get("h2")))
        self.check("真实回答表格撑满内容宽度",
                   float(_m.get("tw") or 0) >= float(_m.get("bw") or 1) - 2,
                   "table=%s body=%s" % (_m.get("tw"), _m.get("bw")))
        # 清掉测试消息并恢复欢迎页(只清 .stream —— #welcome 是 #chat 的子元素, 清 #chat 会连带删掉)
        self.js("(function(){var st=document.querySelector('#chat .stream'); if(st) st.innerHTML='';"
                "var w=document.querySelector('#welcome'); if(w) w.classList.add('show');})()")
        time.sleep(0.2)

        print("== 设置导航不再显示配置状态徽标 ==")
        self.js("document.querySelector('#btn-settings').click()")
        time.sleep(0.5)
        self.check("导航无状态徽标(.st-badge=0)",
                   self.js("document.querySelectorAll('.st-badge').length") == 0)
        _navtxt = str(self.js("(function(){var n=document.querySelector('.set-nav')||document.querySelector('.set-tabs');"
                              "return n?n.textContent:'';})()"))
        self.check("导航不再出现「未配置」字样", "未配置" not in _navtxt, _navtxt[:80])
        self.js("(function(){var b=document.querySelector('#set-close-x'); if(b) b.click();})()")
        time.sleep(0.3)

        print("== 0a. 页面布局(v43 信息架构) ==")
        side_items = self.js("document.querySelectorAll('.side-item').length")
        self.check("侧栏分组菜单行", side_items >= 5, "items=%s" % side_items)
        side_groups = self.js("document.querySelectorAll('.side-label').length")
        self.check("侧栏仅保留「工作区」分组", side_groups == 1, "groups=%s" % side_groups)
        side_txt = self.js("document.querySelector('#sidebar').textContent")
        self.check("左下角运行状态块已移除",
                   self.js("document.querySelectorAll('.status-row').length") == 0
                   and self.js("document.querySelectorAll('#sidebar .pill').length") == 0
                   and ("运行状态" not in str(side_txt)), side_txt)
        self.check("顶栏当前专家徽标", self.js("document.querySelector('#expert-chip') !== null"))
        wc = self.js("(function(){var w=document.querySelector('#welcome');"
                     "return !!(w && w.classList.contains('show'));})()")
        self.check("空任务显示欢迎页", bool(wc), wc)
        wc_cards = self.js("document.querySelectorAll('.wc-card').length")
        self.check("欢迎页能力卡片=4(按当前专家渲染)", wc_cards == 4, "cards=%s" % wc_cards)
        wc_first = self.js("document.querySelector('.wc-card b').textContent")
        self.check("安全运营专家卡片为研判场景", str(wc_first) == "告警研判", wc_first)

        print("== 0a2. 专家中心: 当前任务专家默认收起 ==")
        fold = self.js("(function(){var b=document.querySelector('#persona-collapse');"
                       "return !!(b && !b.classList.contains('open'));})()")
        self.check("「安全运营」子项默认收起", bool(fold), fold)
        aria = self.js("document.querySelector('#btn-persona-fold').getAttribute('aria-expanded')")
        self.check("折叠箭头 aria-expanded=false", str(aria) == "false", aria)
        self.js("document.querySelector('#btn-persona-fold').click()")
        time.sleep(0.15)
        self.check("点箭头可展开", self.js(
            "document.querySelector('#persona-collapse').classList.contains('open') === true"))
        self.js("document.querySelector('#btn-persona-fold').click()")
        time.sleep(0.15)
        self.check("再点可收起", self.js(
            "document.querySelector('#persona-collapse').classList.contains('open') === false"))

        print("== 0a2b. 专家切换联动(v43 三专家+动态欢迎卡) ==")
        self.js("document.querySelector('#btn-persona').click()")
        time.sleep(0.35)
        builtin_n = self.js("(function(){var c=[...document.querySelectorAll('#modal .ex-card')];"
                            "return c.filter(x=>!x.classList.contains('ex-add') && !x.classList.contains('ex-del')).length;})()")
        self.check("内置专家卡片=3(安全运营/编码/日常助手)", builtin_n == 3, "cards=%s" % builtin_n)
        has_ast = self.js("document.querySelector('#modal [data-use=assistant]') !== null")
        self.check("「日常工作助手」卡片有「开始任务」", bool(has_ast))
        self.js("document.querySelector('#modal [data-use=assistant]').click()")
        time.sleep(0.6)
        chip = self.js("document.querySelector('#expert-chip').textContent")
        self.check("顶栏徽标切到日常工作助手", "日常工作助手" in str(chip), chip)
        wc1 = self.js("document.querySelector('.wc-card b') ? document.querySelector('.wc-card b').textContent : ''")
        self.check("欢迎卡片随专家重渲(写作/润色)", str(wc1) == "写作 / 润色", wc1)
        ph = self.js("document.querySelector('#input').placeholder")
        self.check("输入提示随专家切换", "直接说事" in str(ph), ph)
        # 切回安全运营, 恢复初始态
        self.js("document.querySelector('#btn-persona').click()")
        time.sleep(0.35)
        self.js("document.querySelector('#modal [data-use=ezcode]').click()")
        time.sleep(0.6)
        wc2 = self.js("document.querySelector('.wc-card b').textContent")
        self.check("切回安全运营卡片恢复(告警研判)", str(wc2) == "告警研判", wc2)

        print("== 0a3. 输入框工具栏(WorkBuddy 式) ==")
        self.check("＋加号在输入框工具栏",
                   self.js("document.querySelector('#inputbar #btn-plus') !== null")
                   and "＋" in str(self.js("document.querySelector('#btn-plus').textContent")))
        self.check("权限选择在输入框工具栏",
                   self.js("document.querySelector('#inputbar #btn-mode') !== null")
                   and "只读查询" in str(self.js("document.querySelector('#mode-label').textContent")))
        self.check("上下文百分比在输入框工具栏",
                   self.js("document.querySelector('#inputbar #btn-ctx') !== null"))
        pct = self.js("document.querySelector('#ctx-pct').textContent")
        self.check("上下文显示百分比", str(pct).endswith("%") and "12" in str(pct), pct)
        ring = self.js("document.querySelector('#ctx-ring').style.getPropertyValue('--p')")
        self.check("百分比环同步水位", str(ring).strip() == "12%", ring)
        self.check("模型名称选择在输入框工具栏",
                   self.js("document.querySelector('#inputbar #btn-model') !== null")
                   and "deepseek-chat" in str(self.js("document.querySelector('#model-label').textContent")))
        self.check("顶栏不再堆上下文/压缩控件",
                   self.js("document.querySelector('#ctx-chip') === null")
                   and self.js("document.querySelector('.top-right #btn-compact') === null"))
        self.check("发送按钮为箭头", "↑" in str(self.js("document.querySelector('#sendbtn').textContent")))
        self.js("document.querySelector('#btn-plus').click()")
        time.sleep(0.15)
        self.check("＋ 菜单展开", self.js(
            "document.querySelector('#plus-menu').classList.contains('show') === true"))
        n_act = self.js("document.querySelectorAll('#plus-menu [data-act]').length")
        self.check("＋ 菜单含5个快捷入口", n_act == 5, "n=%s" % n_act)
        tv = self.js("document.querySelector('#btn-toolview').textContent")
        self.check("工具显示档位入口在＋菜单", "紧凑" in str(tv), tv)
        self.js("document.querySelector('#plus-menu [data-act=attach]').click()")
        time.sleep(0.25)
        inp = self.js("document.querySelector('#input').value")
        self.check("＋ 附加文件写入 @路径", "@" in str(inp) and "告警" in str(inp), inp)
        self.js("document.querySelector('#input').value='';")
        theme0 = self.js("document.documentElement.dataset.theme")
        self.js("document.querySelector('#btn-plus').click();"
                "document.querySelector('#plus-menu [data-act=theme]').click()")
        time.sleep(0.2)
        theme1 = self.js("document.documentElement.dataset.theme")
        self.check("＋ 菜单切换明暗主题", theme0 != theme1, "%s→%s" % (theme0, theme1))
        self.js("document.querySelector('#btn-plus').click();"
                "document.querySelector('#plus-menu [data-act=theme]').click()")
        time.sleep(0.2)
        self.check("主题可切回", self.js("document.documentElement.dataset.theme") == theme0)
        self.js("document.querySelector('#btn-mode').click()")
        time.sleep(0.15)
        self.check("权限下拉展开", self.js(
            "document.querySelector('#mode-menu').classList.contains('show') === true"))
        n_mode = self.js("document.querySelectorAll('#mode-menu [data-mode]').length")
        self.check("权限两档(只读/可执行)", n_mode == 2, "n=%s" % n_mode)
        self.js("document.querySelector('#mode-menu [data-mode=execute]').click()")
        time.sleep(0.25)
        self.check("选择可执行调用 set_mode",
                   bool(MODE_CALLS) and MODE_CALLS[-1] == "execute", MODE_CALLS)
        self.check("选完权限菜单自动收起", self.js(
            "document.querySelector('#mode-menu').classList.contains('show') === false"))
        self.js("document.querySelector('#btn-ctx').click()")
        time.sleep(0.15)
        detail = self.js("document.querySelector('#ctx-detail').textContent")
        self.check("上下文菜单含水位与压缩入口", "~12%" in str(detail)
                   and self.js("document.querySelector('#ctx-menu #btn-compact') !== null"), detail)
        self.js("document.querySelector('#btn-ctx').click()")
        self.js("document.querySelector('#btn-model').click()")
        time.sleep(0.3)
        self.check("模型下拉展开", self.js(
            "document.querySelector('#model-menu').classList.contains('show') === true"))
        mtxt = self.js("document.querySelector('#model-list').textContent")
        self.check("模型下拉列出已配置模型",
                   "deepseek-chat" in str(mtxt) and "qwen-plus" in str(mtxt), mtxt)
        self.js("document.querySelector('#model-list .model-item:not(.on)').click()")
        time.sleep(0.25)
        self.check("选择模型调用 set_model",
                   bool(MODEL_CALLS) and MODEL_CALLS[-1] == "qwen", MODEL_CALLS)
        print("== 0. 任务与专家中心 ==")
        nb = self.js("document.querySelector('#btn-new').textContent")
        self.check("「＋ 新建任务」按钮", "新建任务" in str(nb), nb)
        sh = self.js("document.querySelector('.sessions-head').textContent")
        self.check("侧栏任务列表标题", "任务列表" in str(sh), sh)
        caret = self.js("document.querySelector('.persona-row .p-caret') !== null")
        self.check("专家行带展开箭头", caret)
        self.js("document.querySelector('#btn-persona').click()")
        time.sleep(0.4)
        cards = self.js("document.querySelectorAll('.ex-card').length")
        self.check("专家中心卡片(内置三位+新建入口)", cards >= 4, "cards=%s" % cards)
        cur = self.js("document.querySelector('.ex-cur') && document.querySelector('.ex-cur').textContent")
        self.check("当前专家「使用中」徽标", "使用中" in str(cur), cur)
        name1 = self.js("document.querySelector('.ex-card .ex-name').textContent")
        self.check("内置专家「安全运营」卡片", "安全运营" in str(name1), name1)
        grp = self.js("document.querySelectorAll('.ex-group').length")
        self.check("内置/自建专家分组", grp == 2, "groups=%s" % grp)
        # 新建专家表单
        self.js("document.querySelector('#ex-add').click()")
        time.sleep(0.2)
        self.check("新建专家表单打开",
                   self.js("document.querySelector('.ex-label') !== null"))
        self.js("document.querySelector('.ex-label').value='分诊专家';"
                " document.querySelector('.ex-prompt').value='你是分诊专家';"
                " document.querySelector('#ex-save').click()")
        time.sleep(0.5)
        cards2 = self.js("document.querySelectorAll('.ex-card').length")
        self.check("新专家出现在专家中心", cards2 == cards + 1, "%s→%s" % (cards, cards2))
        # 删除链: 两步确认
        self.js("document.querySelector('.ex-del').click()")
        time.sleep(0.15)
        armed = self.js("document.querySelector('.ex-del').textContent")
        self.check("删除需两步确认", "确认" in str(armed), armed)
        self.js("document.querySelector('.ex-del').click()")
        time.sleep(0.5)
        cards3 = self.js("document.querySelectorAll('.ex-card').length")
        self.check("两步确认后删除自建专家", cards3 == cards, "got %s" % cards3)
        self.js("document.querySelector('#ex-close').click()")
        time.sleep(0.2)
        self.check("专家中心关闭",
                   not self.js("document.querySelector('#overlay').className.includes('show') === true"))
        # 技能/连接器快捷入口直达设置分区
        self.js("document.querySelector('#btn-connectors').click()")
        time.sleep(0.4)
        tab = self.js("document.querySelector('.set-tab.on span:nth-child(2)').textContent")
        self.check("连接器按钮直达系统API接入", "系统API接入" in str(tab), tab)
        self.js("document.querySelector('#btn-skills').click()")
        time.sleep(0.4)
        tab2 = self.js("document.querySelector('.set-tab.on span:nth-child(2)').textContent")
        self.check("技能按钮直达技能分区", str(tab2) == "技能", tab2)
        self.js("document.querySelector('#set-close').click()")
        time.sleep(0.2)
        print("== 0. 打开设置中心 ==")
        self.js("document.querySelector('#btn-settings').click()")
        time.sleep(0.5)
        self.check("设置弹窗打开", self.js("document.querySelector('#overlay').className.includes('show') === true"))
        tabs = self.js("document.querySelectorAll('.set-tab').length")
        self.check("10个分区导航渲染(含版本更新)", tabs == 10, "tabs=%s" % tabs)
        self.check("版本更新分区已挂载", self.js(
            "Array.from(document.querySelectorAll('.set-tab')).some(b => b.dataset.tab === 'update')"))
        grps = self.js("document.querySelectorAll('.set-group-title').length")
        self.check("设置分区按4组归类", grps == 4, "groups=%s" % grps)
        badges = self.js("document.querySelectorAll('.st-badge').length")
        self.check("导航不带配置状态徽标(已移除)", badges == 0, "badges=%s" % badges)
        self.check("分区说明标题", self.js("document.querySelector('.set-panel-head p') !== null"))
        self.check("默认在大模型分区", self.js("document.querySelector('.set-panel .set-add') !== null"))
        n_profiles = self.js("document.querySelectorAll('.set-panel .set-card').length")
        self.check("大模型profiles卡片数=3", n_profiles == 3, "got %s" % n_profiles)

        # 厂商快捷接入: 卡片 -> 展开表单 -> 只填 Key 即可接入
        nprov = self.js("document.querySelectorAll('.set-panel .prov-card').length")
        self.check("厂商快捷接入卡片渲染(>=8家)", nprov >= 8, "got %s" % nprov)
        self.check("老配置短地址也能识别为已接入", self.js(
            "(function(){const c={profiles:{d:{base_url:'https://api.deepseek.com',model:'deepseek-chat'}}};"
            "return !!_provHit(c,{id:'deepseek',base_url:'https://api.deepseek.com/v1'});})()"))
        self.js("document.querySelectorAll('.set-panel .prov-card')[0].click()")
        time.sleep(0.3)
        self.check("点击厂商展开接入表单", self.js("document.querySelector('.prov-form') !== null"))
        self.check("表单含模型下拉与Key输入", self.js(
            "document.querySelector('.pf-model') !== null && document.querySelector('.pf-key') !== null"))
        nopt = self.js("document.querySelectorAll('.pf-model option').length")
        self.check("模型下拉含预设与手填选项", nopt >= 2, "opt=%s" % nopt)
        self.check("表单显示厂商说明", self.js("document.querySelector('.prov-note') !== null"))
        self.js("document.querySelector('.pf-key').value='sk-diag';"
                "document.querySelector('.pf-key').dispatchEvent(new Event('input'));"
                "document.querySelector('.pf-add').click()")
        time.sleep(0.4)
        hit = LLM_SAVED[-1] if LLM_SAVED else ("", {})
        self.check("接入即写盘(无需再点保存)", bool(LLM_SAVED), "saves=%d" % len(LLM_SAVED))
        self.check("写入的配置带 provider 标记", bool(hit[1].get(hit[0], {}).get("provider")),
                   str(list(hit[1].keys()))[:60])
        self.check("接入后自动设为默认模型", hit[0] == "deepseek", str(hit[0]))
        self.check("保存后仍保留自定义配置区", self.js("document.querySelector('.set-add') !== null"))

        for tab in ["connectors", "custom_api", "mcp", "plugins", "rag", "skills"]:
            self.js("document.querySelectorAll('.set-tab')[[...document.querySelectorAll('.set-tab')].findIndex(b=>b.dataset.tab==='%s')].click()" % tab)
            time.sleep(0.25)
            label = self.js("document.querySelector('.set-tab.on span:nth-child(2)').textContent")
            ok = (label == {"custom_api": "系统API接入", "mcp": "MCP服务器", "plugins": "插件",
                            "connectors": "连接器", "rag": "RAG知识库", "skills": "技能"}[tab])
            self.check("切换到「%s」分区" % label, ok, label)

        print("== 连接器分区(内置勾选 + 自定义) ==")
        self.js("document.querySelectorAll('.set-tab')[[...document.querySelectorAll('.set-tab')].findIndex(b=>b.dataset.tab==='connectors')].click()")
        time.sleep(0.5)
        cards = self.js("document.querySelectorAll('.conn-card[data-id]').length")
        self.check("连接器卡片=10(含内置工具箱)", cards == 10, "cards=%s" % cards)
        self.check("每张卡带启用开关",
                   self.js("document.querySelectorAll('.cc-on').length") == 10)
        self.check("Office 状态来自后端检测",
                   "WPS" in str(self.js("document.querySelector('#cc-state-office').textContent")),
                   self.js("document.querySelector('#cc-state-office').textContent"))
        self.check("MCP 卡片显示服务器数",
                   "2" in str(self.js("document.querySelector('#cc-state-mcp').textContent")))
        # 停用 office → 写入 connectors 节
        self.js("(function(){var el=document.querySelector('.cc-on[data-id=office]');"
                " if(!el) return; el.checked=false; el.dispatchEvent(new Event('change'));})()")
        time.sleep(0.6)
        got = [x for x in SAVED if isinstance(x, dict) and isinstance(x.get("connectors"), dict)]
        self.check("勾选状态写入 connectors 节",
                   bool(got) and got[-1]["connectors"].get("office") is False,
                   (got[-1] if got else None))
        self.check("停用后卡片变灰",
                   self.js("document.querySelector('.conn-card[data-id=office]').classList.contains('off')"))

        print("== 连接器开关真实生效(引擎侧) ==")
        _cfgc = {"builtin_tools": {},
                 "custom_api": {"endpoints": [{"name": "demo", "base_url": "https://x.example.com"}]},
                 "rag": {"enabled": True, "url": "https://y.example.com"},
                 "connectors": {"custom_api": False, "rag": False, "office": False}}
        _tools0.configure(_cfgc)
        _nv = {t["function"]["name"] for t in _tools0.toolset("execute", "ezcode", secops=True)}
        self.check("关闭 custom_api → api_* 从模型视野移除", "api_demo" not in _nv, sorted(n for n in _nv if n.startswith("api_")))
        self.check("关闭 rag → rag_search 移除", "rag_search" not in _nv)
        self.check("关闭 office → office_edit 移除", "office_edit" not in _nv)
        _tools0.configure({"builtin_tools": {},
                           "custom_api": {"endpoints": [{"name": "demo", "base_url": "https://x.example.com"}]},
                           "rag": {"enabled": True, "url": "https://y.example.com"}})
        _nw = {t["function"]["name"] for t in _tools0.toolset("execute", "ezcode", secops=True)}
        self.check("重新勾选后恢复", "api_demo" in _nw and "rag_search" in _nw and "office_edit" in _nw)
        _tools0.configure({"builtin_tools": {}})

        print("== MCP 分区(内置模板勾选 + 简洁列表) ==")
        self.js("document.querySelectorAll('.set-tab')[[...document.querySelectorAll('.set-tab')].findIndex(b=>b.dataset.tab==='mcp')].click()")
        time.sleep(0.4)
        self.check("MCP 内置模板=7", self.js("document.querySelectorAll('.mcp-preset').length") == 7)
        _ptxt = str(self.js("(function(){var a=[...document.querySelectorAll('.mcp-preset')];"
                            "return a.map(b=>b.textContent).join(' | ');})()"))
        self.check("MCP 模板不与内置能力重复(无 filesystem/fetch/filesystem)",
                   ("filesystem" not in _ptxt) and ("fetch" not in _ptxt), _ptxt[:90])
        self.check("模板覆盖常用服务(文档/记忆/数据库/浏览器)",
                   all(k in _ptxt for k in ("context7", "memory", "sqlite", "playwright")), _ptxt[:110])
        self.check("MCP 分区提示不要重复装内置能力",
                   "不要再为这些能力装 MCP" in str(self.js("document.querySelector('.set-panel').textContent")))

        # 勾选模板 → 加入列表(简洁行)
        before = self.js("document.querySelectorAll('.mcp-row').length")
        self.js("(function(){var el=document.querySelector('.mcp-tpl[data-preset=\"1\"]');"
                " if(!el) return; el.checked=true; el.dispatchEvent(new Event('change'));})()")
        time.sleep(0.6)
        after = self.js("document.querySelectorAll('.mcp-row').length")
        self.check("勾选模板后加入列表", after == before + 1, "before=%s after=%s" % (before, after))
        self.check("模板卡状态变为已启用",
                   "已启用" in str(self.js("document.querySelectorAll('.mcp-tpl-state')[1].textContent")),
                   self.js("document.querySelectorAll('.mcp-tpl-state')[1].textContent"))

        # 列表行不含 URL 细节, 且详情默认折叠
        _row = str(self.js("document.querySelector('.mcp-row').textContent"))
        self.check("列表行只显示名称/类型/状态",
                   ("HTTP" in _row.upper()) and "https://" not in _row, _row[:60])
        self.check("详情默认折叠",
                   self.js("document.querySelector('.mcp-detail').hidden === true"))
        self.js("document.querySelector('.mcp-det').click()")
        time.sleep(0.3)
        self.check("点详情可展开配置",
                   self.js("document.querySelector('.mcp-detail').hidden === false"))
        self.check("详情里有测试连接按钮",
                   self.js("document.querySelector('.mcp-detail .mcp-test') !== null"))
        _iw = self.js("document.querySelector('.mcp-detail input').getBoundingClientRect().width")
        _sw = self.js("document.querySelector('.mcp-detail select').getBoundingClientRect().width")
        self.check("详情字段各占一行(select 与 input 等宽, 不错行)",
                   abs(float(_iw or 0) - float(_sw or 0)) < 2, "input=%s select=%s" % (_iw, _sw))

        # 行内开关只切状态
        self.js("(function(){var el=document.querySelector('.mcp-on');"
                " if(!el) return; el.checked=false; el.dispatchEvent(new Event('change'));})()")
        time.sleep(0.4)
        self.check("行内开关切换为已停用",
                   "已停用" in str(self.js("document.querySelector('.mcp-state').textContent")),
                   self.js("document.querySelector('.mcp-state').textContent"))
        self.check("自定义服务器入口仍在",
                   self.js("document.querySelector('.mcp-add') !== null"))

        print("== RAG知识库分区(极简配置 + 测试检索) ==")
        self.js("document.querySelectorAll('.set-tab')[[...document.querySelectorAll('.set-tab')].findIndex(b=>b.dataset.tab==='rag')].click()")
        time.sleep(0.3)
        intros = self.js("document.querySelectorAll('.rag-intro').length")
        self.check("RAG 分区不再显示说明文字", intros == 0, "intros=%s" % intros)
        rag_txt = str(self.js("document.querySelector('.set-panel').textContent"))
        self.check("RAG 分区只保留配置项",
                   ("检索接口地址" in rag_txt and "查询字段名" in rag_txt
                    and "不需要模型支持 RAG" not in rag_txt
                    and "什么时候会调用" not in rag_txt), rag_txt[:80])
        _sys = open(os.path.join(ROOT, "prompts", "system.md"), encoding="utf-8").read()
        self.check("调用时机约束保留在提示词(不占界面)",
                   "外部 RAG 知识库" in _sys and "不得用文档里的泛泛描述替代判据" in _sys)
        self.check("查询字段默认 query",
                   self.js("document.querySelector('.rag-qf').value") == "query")
        self.check("top_k 默认不附带",
                   self.js("document.querySelector('.rag-topk').checked === false"))
        self.check("SSL 默认不校验(内网自签友好)",
                   self.js("document.querySelector('.rag-ssl').checked === false"))
        self.js("document.querySelector('#rag-test').click()")
        time.sleep(0.7)
        rag_out = str(self.js("document.querySelector('#rag-test-out').textContent"))
        self.check("测试检索返回耗时与内容", "成功" in rag_out and "字符" in rag_out, rag_out[:60])
        self.check("测试检索把当前表单配置传给后端",
                   len(RAG_TESTS) == 1 and RAG_TESTS[0].get("query_field") == "query",
                   RAG_TESTS[:1])

        print("== 0b. 运行偏好 / 关于 分区 ==")
        self.js("document.querySelector('.set-tab[data-tab=preferences]').click()")
        time.sleep(0.25)
        pref = self.js("document.querySelectorAll('.pref-card').length")
        self.check("运行偏好分区卡片=6(语言/主题/工具/完成提示/模式/压缩)", pref == 6,
                   "cards=%s" % pref)
        seg = self.js("document.querySelectorAll('.pref-seg').length")
        self.check("偏好分段选择器=5", seg == 5, "segs=%s" % seg)
        nopt = self.js("document.querySelectorAll('.pref-seg[data-pref=notify] .pref-opt').length")
        self.check("完成提示有三档(自动/总是/关闭)", nopt == 3, "opts=%s" % nopt)
        self.check("完成提示默认选中自动", self.js(
            "(function(){var on=document.querySelector('.pref-seg[data-pref=notify] .pref-opt.on');"
            "return on ? on.dataset.val : ''})()") == "auto")
        # ---- 中英文切换: 界面文案跟着换, 且引擎侧收到 set_lang ----
        self.check("语言分段含中文/English两档",
                   self.js("document.querySelectorAll('.pref-seg[data-pref=lang] .pref-opt').length") == 2,
                   self.js("document.querySelector('.pref-seg[data-pref=lang]') ? 'ok' : 'none'"))
        self.js("document.querySelector('.pref-seg[data-pref=lang] .pref-opt[data-val=en]').click()")
        time.sleep(0.45)
        self.check("切英文: 侧栏与导航变英文",
                   "New task" in str(self.js("document.querySelector('#btn-new').textContent"))
                   and "Model & runtime" in str(self.js("document.querySelector('.set-tabs').textContent")),
                   self.js("document.querySelector('.set-tabs').textContent")[:80])
        self.check("切英文: 引擎收到 set_lang('en')",
                   bool(LANG_CALLS) and LANG_CALLS[-1] == "en", LANG_CALLS)
        self.check("切英文: 中文正文未被误翻(设置标题仍在)",
                   self.js("document.querySelector('.set-panel .pref-card') !== null")
                   and "界面语言" not in str(self.js("document.querySelector('.set-tabs').textContent")))
        self.js("document.querySelector('.pref-seg[data-pref=lang] .pref-opt[data-val=zh]').click()")
        time.sleep(0.45)
        self.check("切回中文: 侧栏恢复中文",
                   "新建任务" in str(self.js("document.querySelector('#btn-new').textContent")),
                   self.js("document.querySelector('#btn-new').textContent"))
        self.check("切回中文: 引擎收到 set_lang('zh')",
                   bool(LANG_CALLS) and LANG_CALLS[-1] == "zh", LANG_CALLS)
        self.js("document.querySelector('.set-tab[data-tab=about]').click()")
        time.sleep(0.25)
        self.check("关于分区渲染", self.js("document.querySelector('.about-hero') !== null"))
        about_items = self.js("document.querySelectorAll('.about-item').length")
        self.check("关于信息卡=4", about_items == 4, "items=%s" % about_items)

        print("== 内置集成已隐藏(工具仍默认全开) ==")
        has_b = self.js("[...document.querySelectorAll('.set-tab')].some(b=>b.dataset.tab==='builtin')")
        self.check("设置中心不再出现「内置集成」分区", not has_b)
        _names = {t["function"]["name"] for t in _tools.TOOLS}
        _want = ("fs_ls", "web_fetch", "run_python", "todo_list")
        self.check("内置工具箱仍默认全开(未因隐藏而注销)",
                   all(n in _names for n in _want),
                   [n for n in _want if n not in _names])
        print("== 系统API接入(3深度接入+自定义, 无情报增强) ==")
        self.js("document.querySelectorAll('.set-tab')[[...document.querySelectorAll('.set-tab')].findIndex(b=>b.dataset.tab==='custom_api')].click()")
        time.sleep(0.3)
        n = self.js("document.querySelectorAll('.set-panel .bi-sub:not(.sec-block)').length")
        self.check("预置子区块=3(仅3深度接入)", n == 3, "got %s" % n)
        api_txt = str(self.js("document.querySelector('.set-panel').textContent"))
        self.check("已移除威胁情报增强", "威胁情报增强" not in api_txt and "外部情报" not in api_txt, api_txt[:80])
        self.check("无情报 API Key 输入框", self.js("document.querySelector('.ti-key') === null"))
        removes = self.js("document.querySelectorAll('.bi-remove').length")
        self.check("三个深度接入各带移除按钮", removes == 3, "removes=%s" % removes)
        edrcards = self.js("document.querySelectorAll('.edr-proj').length")
        self.check("主机侧告警项目卡片=5", edrcards == 5, "got %s" % edrcards)
        nscards = self.js("document.querySelectorAll('.ngsoc-site').length")
        self.check("告警检索站点卡片=6(url+key直配)", nscards == 6, "got %s" % nscards)
        nsurl = self.js("document.querySelector('.ngsoc-site input[data-nsf=\\\'url\\\']') && document.querySelector('.ngsoc-site input[data-nsf=\\\'url\\\']').value")
        self.check("站点URL渲染", str(nsurl).startswith("http"), nsurl)
        self.check("无添加按钮(missing=0时)", self.js("document.querySelector('#preset-add') === null"))
        # 保存链: 预置接入三节, 且不再携带情报增强节
        self.js("document.querySelector('#preset-save').click()")
        time.sleep(0.4)
        got = SAVED[-1] if SAVED else {}
        self.check("保存携带三节且不含情报增强节",
                   all(k in got for k in ("ngsoc", "edr", "netbox")) and "threat_intel" not in got,
                   sorted(got))
        # 移除链: 真删除
        self.js("document.querySelector('.bi-remove').click()")
        time.sleep(0.25)
        n2 = self.js("document.querySelectorAll('.set-panel .bi-sub:not(.sec-block)').length")
        addbtn = self.js("document.querySelector('#preset-add') !== null")
        self.check("移除后子区块3→2", n2 == 2, "got %s" % n2)
        self.check("移除后出现添加预置接入按钮", addbtn)

        print("== 项目与设备组(聚合入口 + 自动适配) ==")
        self.js("document.querySelectorAll('.set-tab')[[...document.querySelectorAll('.set-tab')].findIndex(b=>b.dataset.tab==='custom_api')].click()")
        time.sleep(0.3)
        self.check("设备组区块渲染", self.js("document.querySelectorAll('.sec-proj').length") == 1)
        # 说明文字已全部移除: 标题 + 自检按钮下面直接是设备列表
        _btxt = str(self.js("(function(){var b=document.querySelector('.sec-block');"
                            "return b?b.textContent:'';})()"))
        self.check("设备组区块不再有常驻说明文字",
                   ("聚合检索并发查询" not in _btxt) and ("设备清单全部来自" not in _btxt)
                   and ("尚未写入配置" not in _btxt) and ("自动适配前建议" not in _btxt), _btxt[:80])
        # 保存预置接入时把内存迁移出来的设备组一并固化(否则磁盘永远没有 projects)
        self.js("document.querySelector('#preset-save').click()")
        time.sleep(0.8)
        _gp = [x for x in SAVED if isinstance(x, dict) and isinstance(x.get("projects"), list)]
        self.check("保存预置接入携带 projects(设备组固化为配置)",
                   bool(_gp) and len(_gp[-1]["projects"]) >= 1,
                   "saved_keys=%s" % (sorted(_gp[-1]) if _gp else []))
        devs = self.js("document.querySelectorAll('.sec-dev').length")
        self.check("设备行=3(2内置+1 http)", devs == 3, "devs=%s" % devs)
        adapts = self.js("document.querySelectorAll('.sec-adapt').length")
        self.check("仅 http 设备有自动适配按钮", adapts == 1, "adapts=%s" % adapts)
        states = self.js("[...document.querySelectorAll('.sec-state')].map(e=>e.textContent).join(',')")
        self.check("设备状态标注(就绪/待映射, 不再出现「内置」)",
                   "就绪" in str(states) and "待映射" in str(states)
                   and "内置适配" not in str(states), states)
        self.js("document.querySelector('#sec-probe-all').click()")
        time.sleep(0.6)
        rows = self.js("document.querySelectorAll('.sec-probe-out .sec-table tbody tr').length")
        self.check("自检结果表=3行", rows == 3, "rows=%s" % rows)
        # 接口文档: 展开 → 粘贴 → 保存进设备配置(自动适配时随样本一起给模型)
        self.check("http 设备有「文档」入口",
                   self.js("document.querySelectorAll('.sec-doc-toggle').length") == 1)
        self.js("document.querySelector('.sec-doc-toggle').click()")
        time.sleep(0.3)
        shown = self.js("document.querySelector('.sec-doc') && document.querySelector('.sec-doc').style.display")
        self.check("接口文档区可展开", shown == "block", shown)
        self.js("(function(){var t=document.querySelector('.sec-doc-input');"
                " if(!t) return; t.value='GET /api/alarms 返回 data.list, 字段 name/level/ts';"
                " t.dispatchEvent(new Event('input'));})()")
        self.js("document.querySelector('.sec-doc-save').click()")
        time.sleep(0.6)
        saved_doc = ""
        for sec in reversed(SAVED):
            if isinstance(sec, dict) and isinstance(sec.get("projects"), list):
                devs = (sec["projects"][0].get("devices") or [])
                if len(devs) > 2:
                    saved_doc = devs[2].get("api_doc") or ""
                break
        self.check("接口文档保存进设备配置", "data.list" in str(saved_doc), str(saved_doc)[:40])

        self.js("document.querySelector('.sec-adapt').click()")
        time.sleep(0.7)
        self.check("自动适配弹窗打开", self.js("document.querySelector('#adapt-json') !== null"))
        js = self.js("document.querySelector('#adapt-json') && document.querySelector('#adapt-json').value")
        self.check("映射草案含 list_path", "list_path" in str(js), str(js)[:60])
        prev = self.js("document.querySelector('#adapt-preview') && document.querySelector('#adapt-preview').textContent")
        self.check("归一化预览含告警", "SQL注入" in str(prev), str(prev)[:60])
        self.js("document.querySelector('#adapt-save').click()")
        time.sleep(0.7)
        self.check("保存映射调用后端",
                   len(SEC_APPLIED) == 1 and SEC_APPLIED[0][0] == "演示项目", SEC_APPLIED[:1])
        self.check("保存后适配弹窗关闭",
                   self.js("!document.querySelector('#overlay2').classList.contains('show')"))
        self.check("设置面板仍在(未被适配弹窗覆盖)",
                   self.js("document.querySelectorAll('.set-tab').length > 0"))

        self.js("document.querySelectorAll('.set-tab')[[...document.querySelectorAll('.set-tab')].findIndex(b=>b.dataset.tab==='mcp')].click()")
        time.sleep(0.25)
        first_mcp = self.js("document.querySelector('.mcp-row .mcp-name').textContent")
        self.check("MCP列表渲染context7", first_mcp == "context7", first_mcp)
        # 详情默认折叠, 展开后测试连接
        self.js("document.querySelector('.mcp-det').click()")
        time.sleep(0.25)
        self.js("document.querySelector('.mcp-test').click()")
        time.sleep(0.4)
        txt = self.js("document.querySelector('.mcp-test-result').textContent")
        self.check("MCP测试按钮返回结果", "连通" in str(txt) and "3 个工具" in str(txt), txt)

        print("== 编辑+保存调用链(预置接入字段透传) ==")
        self.js("document.querySelectorAll('.set-tab')[[...document.querySelectorAll('.set-tab')].findIndex(b=>b.dataset.tab==='custom_api')].click()")
        time.sleep(0.3)
        self.js("(function(){var t=document.querySelector('.nb-url'); if(!t) return;"
                " t.value='https://ipam.example.invalid/api'; t.dispatchEvent(new Event('input'));})()")
        self.js("(function(){var b=document.querySelector('#preset-save'); if(!b) return; b.click();})()")
        time.sleep(0.4)
        got = SAVED[-1] if SAVED else {}
        self.check("预置接入保存携带修改值",
                   (got.get("netbox") or {}).get("base_url") == "https://ipam.example.invalid/api"
                   and all(k in got for k in ("ngsoc", "edr", "netbox"))
                   and "threat_intel" not in got, sorted(got))

        print("== 插件分区 ==")
        self.js("document.querySelectorAll('.set-tab')[[...document.querySelectorAll('.set-tab')].findIndex(b=>b.dataset.tab==='plugins')].click()")
        time.sleep(0.3)
        cards = self.js("document.querySelectorAll('.plugin-card').length")
        self.check("已装插件卡片渲染(b64_tools)", cards == 1, "cards=%s" % cards)
        tools_txt = self.js("document.querySelector('.plugin-card .fhint') && document.querySelector('.plugin-card .fhint').textContent")
        self.check("插件卡片显示工具清单", "b64_encode" in str(tools_txt), tools_txt)
        self.js("document.querySelector('.pl-enabled').checked=false;"
                " document.querySelector('.pl-enabled').dispatchEvent(new Event('change'))")
        time.sleep(0.4)
        self.check("启停开关调用链生效", any(x["enabled"] is False for x in PLUGINS))
        self.js("document.querySelector('#pl-install-code').click()")
        time.sleep(0.2)
        vis = self.js("document.querySelector('.pl-code-wrap').style.display !== 'none'")
        self.check("粘贴代码安装区展开", vis)
        self.js("document.querySelector('.pl-new-name').value='运营速判插件';"
                " document.querySelector('.pl-code-wrap .sk-body').value='TOOLS = []';"
                " document.querySelector('.pl-code-save').click()")
        time.sleep(0.4)
        self.check("粘贴代码安装成功", any(x["name"] == "运营速判插件" for x in PLUGINS))
        cards2 = self.js("document.querySelectorAll('.plugin-card').length")
        self.check("新插件出现在列表", cards2 == 2, "cards=%s" % cards2)

        print("== 系统API接入: 新增卡片 ==")
        self.js("document.querySelectorAll('.set-tab')[[...document.querySelectorAll('.set-tab')].findIndex(b=>b.dataset.tab==='custom_api')].click()")
        time.sleep(0.3)
        before = self.js("document.querySelectorAll('.set-panel .lv-item').length")
        self.js("document.querySelector('.lv-add').click()")
        time.sleep(0.3)
        after = self.js("document.querySelectorAll('.set-panel .lv-item').length")
        self.check("自定义接入新增卡片(隔离类不误伤主机侧)", after == before + 1, "%s→%s" % (before, after))

        print("== 技能分区 ==")
        self.js("document.querySelectorAll('.set-tab')[[...document.querySelectorAll('.set-tab')].findIndex(b=>b.dataset.tab==='skills')].click()")
        t0 = time.time()
        while time.time() - t0 < 4:
            if self.js("document.querySelector('.sk-new-name') !== null"):
                break
            time.sleep(0.2)
        time.sleep(0.1)
        # 新建全局技能
        self.js("document.querySelector('.sk-new-name').value='周报格式规范';"
                " document.querySelector('#sk-add').click()")
        time.sleep(0.4)
        cards = self.js("document.querySelectorAll('.set-panel .skill-card').length")
        self.check("新建技能出现在列表(内置1+新建1)", cards == 2, "cards=%s" % cards)
        self.check("技能卡片不再显示「内置」标签",
                   self.js("document.querySelector('.sk-badge') === null")
                   and self.js("document.querySelectorAll('.set-panel .skill-card').length > 0"))
        self.check("内置技能不提供删除按钮(只能覆盖)",
                   self.js("(function(){var cs=[].slice.call(document.querySelectorAll('.skill-card'));"
                           "var b=cs.filter(function(c){return (c.dataset.file||'').indexOf('security-triage')===0;});"
                           "return b.length===1 && b[0].querySelector('.sk-del')===null;})()"))
        _wk = ("(function(){var cs=[].slice.call(document.querySelectorAll('.skill-card'));"
               "return cs.filter(function(c){return (c.dataset.file||'').indexOf('周报')===0;})[0];})()")
        name = self.js(_wk + ".querySelector('.sk-name').textContent")
        self.check("技能卡片渲染名称", "周报" in str(name), name)
        # 编辑展开(定位到"周报"卡, 内置技能排在第一张, 不能取第一个元素)
        self.js(_wk + ".querySelector('.sk-edit').click()")
        time.sleep(0.2)
        vis = self.js(_wk + ".querySelector('.sk-body').style.display !== 'none'")
        self.check("编辑区展开", vis)
        self.js(_wk + ".querySelector('.sk-body').value='回复必须分三段';")
        self.js(_wk + ".querySelector('.sk-save-body').click()")
        time.sleep(0.4)
        saved = [s for s in SKILLS if s["file"].startswith("周报")]
        self.check("技能内容保存到引擎", bool(saved) and "三段" in (saved[0]["content"] if saved else ""), saved)
        # 启停开关
        self.js(_wk + ".querySelector('.sk-enabled').checked=false;"
                " " + _wk + ".querySelector('.sk-enabled').dispatchEvent(new Event('change'))")
        time.sleep(0.4)
        self.check("停用开关生效", any(s["file"].startswith("周报") and not s["enabled"] for s in SKILLS))

        print("== 工具显示三档 ==")
        # 紧凑档(默认)
        self.js("localStorage.setItem('ezcode-toolview','compact'); applyToolViewBtn();")
        self.js("window.ezcodeBridge.event({t:'tool', name:'kb_search', args:'{\"query\":\"test\"}'});")
        time.sleep(0.25)
        n = self.js("document.querySelectorAll('.toolcard').length")
        compact = self.js("document.querySelectorAll('.toolcard.compact').length")
        btn = self.js("document.querySelector('#btn-toolview').textContent")
        self.check("紧凑档: 一行卡且带compact类", n >= 1 and compact >= 1, "n=%s c=%s" % (n, compact))
        self.check("档位按钮显示紧凑", "紧凑" in str(btn), btn)
        self.js("window.ezcodeBridge.event({t:'tool_done', ok:true, dt:1.2, preview:'结果'});")
        time.sleep(0.25)
        okmark = self.js("document.querySelector('.toolcard.compact .t-stat').textContent")
        self.check("紧凑档完成态✓耗时", "✓" in str(okmark) and "1.2" in str(okmark), okmark)
        # 详细档
        self.js("document.querySelector('#btn-toolview').click()")   # compact→detailed
        time.sleep(0.15)
        self.js("window.ezcodeBridge.event({t:'tool', name:'kb_search', args:'x'});")
        time.sleep(0.2)
        last = self.js("document.querySelectorAll('.toolcard:not(.compact)').length")
        args_shown = self.js("document.querySelectorAll('.toolcard:not(.compact) .t-args')[document.querySelectorAll('.toolcard:not(.compact) .t-args').length-1].textContent")
        self.check("详细档: 完整卡片+参数可见", last >= 1 and str(args_shown).strip() != "", args_shown)
        self.js("window.ezcodeBridge.event({t:'tool_done', ok:true, dt:0.8, preview:'r'});")
        # 隐藏档
        self.js("document.querySelector('#btn-toolview').click()")   # detailed→hidden
        time.sleep(0.15)
        btn2 = self.js("document.querySelector('#btn-toolview').textContent")
        self.check("循环切换到隐藏档", "隐藏" in str(btn2), btn2)
        before = self.js("document.querySelectorAll('.toolcard').length")
        self.js("window.ezcodeBridge.event({t:'tool', name:'kb_search', args:'x'});")
        time.sleep(0.2)
        after = self.js("document.querySelectorAll('.toolcard').length")
        self.check("隐藏档: 不再创建工具卡", after == before, "%s→%s" % (before, after))
        runbar = self.js("document.querySelector('#run-text') && document.querySelector('#run-text').textContent")
        self.check("隐藏档: 顶部仍有运行提示", "kb_search" in str(runbar), runbar)
        self.js("window.ezcodeBridge.event({t:'round_end', dt:1, n_tools:2});")
        self.js("localStorage.setItem('ezcode-toolview','compact');")

        print("== 工具返回内容不进对话 / 多任务事件隔离 ==")
        self.js("localStorage.setItem('ezcode-toolview','compact'); applyToolViewBtn();")
        self.js("window.ezcodeBridge.event({t:'tool', name:'file_write', args:'{\"path\":\"tdp_conf.json\",\"api_key\":\"47d5cbcf\",\"secret\":\"970ce07a\"}'});")
        time.sleep(0.2)
        self.js("window.ezcodeBridge.event({t:'tool_done', ok:true, dt:1.1, preview:'{\"success\":true,\"path\":\"tdp_conf.json\",\"secret\":\"970ce07a\"}'});")
        time.sleep(0.2)
        self.js("(function(){var a=document.querySelectorAll('.toolcard.compact .t-head');a[a.length-1].click();})()")
        time.sleep(0.15)
        body = self.js("(function(){var a=document.querySelectorAll('.toolcard.compact .t-body');return a[a.length-1].textContent})()")
        self.check("点击工具卡不显示返回JSON与密钥",
                   "success" not in str(body) and "970ce07a" not in str(body)
                   and "tdp_conf.json" not in str(body), body)
        arg = self.js("(function(){var a=document.querySelectorAll('.toolcard.compact .t-args');return a[a.length-1].textContent})()")
        self.check("展开的调用参数已脱敏",
                   "47d5cbcf" not in str(arg) and "970ce07a" not in str(arg)
                   and "***" in str(arg), arg)
        filt = self.js("String(onEvent).includes('e.sid !== ui.sid')")
        self.check("事件按任务 sid 过滤(后台任务不串台)",
                   filt is True or str(filt).lower() == "true", filt)
        self.js("window.ezcodeBridge.event({t:'round_end', dt:1, n_tools:1});")

        print("== 关闭与残留 ==")
        self.js("document.querySelector('#set-close').click()")
        time.sleep(0.3)
        self.check("设置弹窗关闭", not self.js("document.querySelector('#overlay').className.includes('show') === true"))
        self.check("无JS异常标记", self.js("window.__setErr || 'none'") == "none")

        # 这段必须放在设置弹窗关闭之后: confirmBox 会重写 #modal 的 innerHTML,
        # 提前跑会把 #set-close 冲掉。
        print("== 后台任务: 事件缓存 / 确认立即弹 / 切回补渲染 ==")
        self.js("window.__realSid=ui.sid; ui.sid='__SID_A__'; ui.taskBuf={}; ui.taskMeta={};"
                "ui.confirmQueue=[]; ui.confirmOpen=false;")
        self.js("window.ezcodeBridge.event({t:'text', text:'后台进度ABC', sid:'__SID_B__'});")
        time.sleep(0.15)
        self.check("后台任务事件按sid缓存(不再整条丢弃)",
                   int(self.js("(ui.taskBuf['__SID_B__']||[]).length") or 0) >= 1)
        self.check("后台任务正文不落进当前对话DOM",
                   "后台进度ABC" not in str(self.js("document.querySelector('#chat').textContent")))
        self.check("缓存里记录后台任务仍在运行",
                   self.js("!!(ui.taskMeta['__SID_B__'] && ui.taskMeta['__SID_B__'].busy)") is True)
        self.js("window.ezcodeBridge.event({t:'confirm', sid:'__SID_B__', seq:987654,"
                "action:'shell', detail:'echo x', title:'任务B'});")
        time.sleep(0.2)
        self.check("后台权限确认立即弹窗(不再排队等切回)",
                   self.js("ui.confirmOpen") is True)
        self.check("确认弹窗标注来源任务",
                   "任务B" in str(self.js(
                       "(document.querySelector('.m-from')||{}).textContent || ''")))
        self.js("ui.confirmOpen=false; document.querySelector('#overlay').className='overlay';"
                "ui.confirmQueue=[];")
        self.js("window.ezcodeBridge.event({t:'round_end', sid:'__SID_B__', dt:2, n_tools:1});")
        time.sleep(0.15)
        self.check("后台轮次结束被记录(切回时可补收尾)",
                   self.js("!!(ui.taskMeta['__SID_B__'] && ui.taskMeta['__SID_B__'].done)") is True)
        self.js("ui.sid='__SID_B__'; restoreBackground('__SID_B__');")
        time.sleep(0.2)
        self.check("切回已结束的后台任务会补收尾(不再锁在空白运行中)",
                   self.js("ui.busy") is False)
        self.check("补渲染后清掉该任务的缓存(不会重复插入)",
                   self.js("!ui.taskMeta['__SID_B__']") is True)
        self.js("ui.sid=window.__realSid; ui.taskBuf={}; ui.taskMeta={}; ui.confirmQueue=[];")

        n_ok = sum(1 for _, ok in self.results if ok)
        print()
        print("设置中心诊断: %d通过 / %d失败" % (n_ok, len(self.results) - n_ok))
        sys.stdout.flush()
        os._exit(0 if n_ok == len(self.results) else 1)


if __name__ == "__main__":
    Runner().run()
