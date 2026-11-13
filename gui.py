#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EZcode 桌面客户端 (pywebview + WebView2, 现代桌面助手形态)
- 前端 webui.html: 原生文本选中复制 / 真Markdown渲染(无标记符裸露) / 工具证据折叠
- 引擎/工具/权限/验证门 100%复用 agent.py 现有实现
运行: python -X utf8 gui.py
"""
import os
import sys
import json
import time
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import webview

import agent as A
import plugin_loader
from tools import toolset, configure, permissions

SESSIONS_DIR = os.path.join(BASE_DIR, "logs", "sessions")


# ============ 引擎事件 → WebView(节流合并推送) ============
class GuiUI:
    """实现agent.py引擎的ui事件协议(stream/reason/content/tool系列), 批量推给前端"""

    def __init__(self, app):
        self.app = app
        self._q = []
        self._lock = threading.Lock()
        self._ev = threading.Event()
        threading.Thread(target=self._flusher, daemon=True).start()

    def _push(self, fn, arg):
        with self._lock:
            self._q.append((fn, json.dumps(arg, ensure_ascii=False)))
        self._ev.set()

    def _flusher(self):
        while True:
            self._ev.wait()
            self._ev.clear()
            time.sleep(0.05)              # 合并窗口: token级delta批量推
            with self._lock:
                batch, self._q = self._q, []
            for fn, arg in batch:
                try:
                    self.app.window.evaluate_js("ui.%s(%s)" % (fn, arg))
                except Exception:
                    pass

    # ---- 引擎协议 ----
    def stream_start(self):
        self._push("streamStart", {})

    def reason_delta(self, buf):
        self._push("thinkDelta", buf)

    def reason_done(self, total, elapsed):
        self._push("thinkDone", int(total))

    def content_start(self):
        pass

    def content_delta(self, buf):
        self._push("contentDelta", buf)

    def content_end(self, buf):
        self._push("contentEnd", {})

    def tool_start(self, fname, args_str):
        self._push("toolStart", {"name": fname, "args": str(args_str)[:400]})

    def tool_running(self, fname):
        self._push("runState", {"text": "工具: %s" % fname})

    def sub_progress(self, text):
        """子代理进度心跳: 更新状态行(经推送队列, 线程安全)"""
        self._push("runState", {"text": text})

    def tool_done(self, fname, ok, dt, size, err=None):
        head = ""
        try:   # 取最近一条工具结果做证据预览
            for m in reversed(self.app.messages):
                if m.get("role") == "tool":
                    head = str(m.get("content") or "")[:2500]
                    break
        except Exception:
            pass
        self._push("toolDone", {"name": fname, "ok": ok, "dt": "%.1f" % dt,
                                "head": head, "err": err})

    def round_done(self, answer, dt, n_tools, n_think, slog):
        pass   # 轮次收尾由run_round统一推送(避免双触发)


# ============ 应用主体 ============
class SecopGuiApp:
    def __init__(self):
        self.cfg = A.load_config()
        configure(self.cfg)
        A.RUNTIME["cfg"] = self.cfg
        self.profiles, self.active = A.normalize_llm(self.cfg)
        self.model_override = None
        plugin_loader.bootstrap(self.cfg)
        permissions.set_confirm(self.gui_confirm)
        self.messages = [{"role": "system", "content": A.build_system_prompt(self.cfg)}]
        self.slog = A.SessionLog()
        self.mode = "query"
        self.busy = False
        self.session_title = ""
        self._confirm_seq = 0
        self._confirm_waiters = {}
        self._cancel = threading.Event()
        self.ui = GuiUI(self)
        self.window = None

    # ---------- 会话持久化(与TUI同格式, logs/sessions/) ----------
    def _session_file(self):
        return os.path.join(SESSIONS_DIR,
                            os.path.basename(self.slog.path).rsplit(".", 1)[0] + ".session.json")

    def _save_session(self):
        if len([m for m in self.messages if m.get("role") == "user"]) == 0:
            return
        try:
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            data = {"title": self.session_title or "未命名会话",
                    "messages": self.messages, "slog_path": self.slog.path,
                    "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            with open(self._session_file(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=str)
        except Exception:
            pass

    def _disk_sessions(self):
        out = []
        try:
            for fn in sorted(os.listdir(SESSIONS_DIR), reverse=True):
                if not fn.endswith(".session.json"):
                    continue
                with open(os.path.join(SESSIONS_DIR, fn), encoding="utf-8") as f:
                    d = json.load(f)
                n = len([m for m in (d.get("messages") or [])
                         if m.get("role") in ("user", "assistant")])
                if n == 0:
                    continue
                out.append({"id": fn, "title": d.get("title") or "未命名会话",
                            "n_msgs": n, "saved_at": d.get("saved_at", "")})
        except Exception:
            pass
        return out

    # ---------- 研判轮 ----------
    def _effective_profile(self):
        p = dict(self.profiles[self.active])
        if self.model_override:
            p["model"] = self.model_override
        return p

    def _run_round(self, user_text):
        self.messages.append({"role": "user", "content": user_text})
        self._cancel = threading.Event()

        def worker():
            try:
                answer, n_tools, n_think, dt = A.run_agent(
                    self._effective_profile(), self.active, self.messages, self.slog, self.ui,
                    tools_list=toolset(self.mode), allow_exec=(self.mode == "execute"),
                    cancel=self._cancel, session_tool_log=self._session_tool_log)
                ok = {"answer": answer, "dt": "%.1f" % dt, "n_tools": n_tools,
                      "think": format(n_think, ","), "ctx_pct": self._ctx_pct()}
            except A.RoundCancelled:
                ok = {"answer": "", "dt": "0", "n_tools": 0, "think": "0",
                      "ctx_pct": self._ctx_pct(), "cancelled": True}
            except Exception as e:
                ok = {"answer": "⚠ 引擎异常: %s: %s" % (type(e).__name__, e), "dt": "0",
                      "n_tools": 0, "think": "0", "ctx_pct": self._ctx_pct()}
            self.busy = False
            self._save_session()
            try:
                self.window.evaluate_js("ui.roundDone(%s)" % json.dumps(ok, ensure_ascii=False))
            except Exception:
                pass
        self.busy = True
        self._session_tool_log = getattr(self, "_session_tool_log", [])
        threading.Thread(target=worker, daemon=True).start()

    def _ctx_pct(self):
        try:
            total = sum(len(str(m.get("content") or "")) for m in self.messages)
            limit = int(self._effective_profile().get("context") or 128000)
            est_tokens = int(total * 0.55)          # 字符≈0.55 token粗估
            return min(100, est_tokens * 100 // max(limit, 1))
        except Exception:
            return 0

    # ---------- 权限确认桥(引擎线程 → 前端弹窗 → 等待点击) ----------
    def gui_confirm(self, action, resource, detail):
        self._confirm_seq += 1
        seq = self._confirm_seq
        ev = threading.Event()
        box = {"choice": None}
        self._confirm_waiters[seq] = (ev, box)
        try:
            self.window.evaluate_js(
                "ui.confirm(%s)" % json.dumps({"seq": seq, "action": action,
                                               "resource": resource, "detail": detail},
                                              ensure_ascii=False))
        except Exception:
            return False
        ev.wait(timeout=600)          # 10分钟无响应视为拒绝
        self._confirm_waiters.pop(seq, None)
        c = box["choice"]
        if c == "always":
            try:
                permissions.grant_session(action, "*")
            except Exception:
                pass
        return c in ("once", "always")

    # ================= js_api (前端调用) =================
    def ping(self):
        return "ok"

    def welcome(self):
        from tools import ngsoc as _ngsoc
        n_sites = len(_ngsoc._sites)
        return ("模型: %s (%s) · %s · 模式: %s · 历史会话自动落盘可恢复"
                % (self._effective_profile().get("model"), self.active,
                   ("%d个告警平台站点" % n_sites) if n_sites else "告警平台未配置",
                   "可执行" if self.mode == "execute" else "只读查询"))

    def history(self):
        """恢复会话后重放消息到前端"""
        out = []
        for m in self.messages:
            r = m.get("role")
            if r == "user":
                out.append({"kind": "user", "text": str(m.get("content") or "")[:8000]})
            elif r == "assistant" and m.get("content"):
                out.append({"kind": "bot", "text": str(m.get("content"))[:12000]})
        return out

    def send(self, text):
        if self.busy:
            return {"error": "busy"}
        text = str(text or "").strip()
        if not text:
            return {"error": "empty"}
        if text.startswith("@") or "@" in text:
            text = A.expand_at_files(text)
        self.slog.log("用户", text)
        self._run_round(text)
        return {"ok": True}

    def stop(self):
        self._cancel.set()
        return {"ok": True}

    def get_state(self):
        return {"mode": self.mode, "model": self._effective_profile().get("model"),
                "active": self.active, "busy": self.busy,
                "n_msgs": len(self.messages), "ctx_pct": self._ctx_pct()}

    def set_mode(self, mode):
        if self.busy:
            return {"error": "busy"}
        self.mode = "execute" if mode == "execute" else "query"
        self.messages.append({"role": "system",
                              "content": "[系统] 用户已切换到%s模式" % ("可执行" if self.mode == "execute" else "只读查询")})
        self.slog.log("系统", "切换模式: %s" % self.mode)
        return {"ok": True}

    def list_models(self):
        return [{"key": name, "model": p.get("model"), "profile": name}
                for name, p in self.profiles.items()]

    def set_model(self, key):
        if key in self.profiles:
            self.active = key
            self.model_override = None
            self.slog.log("系统", "切换模型: %s" % key)
            return {"ok": True}
        return {"error": "unknown"}

    def new_session(self):
        if self.busy:
            return {"error": "busy"}
        self._save_session()
        self.messages = [{"role": "system", "content": A.build_system_prompt(self.cfg)}]
        try:
            self.slog.close()
        except Exception:
            pass
        self.slog = A.SessionLog()
        self.session_title = ""
        self._session_tool_log = []
        return {"ok": True}

    def list_sessions(self):
        return self._disk_sessions()[:20]

    def open_session(self, sid):
        try:
            with open(os.path.join(SESSIONS_DIR, sid), encoding="utf-8") as f:
                d = json.load(f)
            self.messages = d.get("messages") or [{"role": "system", "content": ""}]
            if self.messages and self.messages[0].get("role") == "system":
                self.messages[0]["content"] = A.build_system_prompt(self.cfg)
            # 会话可能保存于工具执行中途(如运行中关窗): 尾部悬挂的assistant(tool_calls)/tool
            # 不清理会在下轮请求报400
            A.repair_messages(self.messages)
            self.session_title = d.get("title") or ""
            self._session_tool_log = []
            try:
                self.slog.close()
            except Exception:
                pass
            sp = d.get("slog_path")
            self.slog = A.SessionLog(path=sp) if sp and os.path.exists(sp) else A.SessionLog()
            self.slog.log("系统", "恢复会话: %s" % self.session_title)
            return {"ok": True}
        except Exception as e:
            return {"error": str(e)[:200]}

    def export_session(self):
        path = A.export_session(self.messages)
        self.slog.log("系统", "导出会话: %s" % path)
        return path

    def compact(self):
        n = A._compact_messages(self.messages)
        return {"ok": bool(n)}

    def copy_msg(self, text):
        """复制按钮 → Win32剪贴板(ctypes, Unicode保真)"""
        ok = False
        if os.name == "nt":
            try:
                from tui import SecOpsApp
                ok = SecOpsApp._win32_copy(str(text or ""))
            except Exception:
                ok = False
        return {"ok": ok}

    def confirm_result(self, seq, choice):
        # 归一化: 旧版前端可能传布尔(True='允许这一次'); 其他值一律视为拒绝
        if choice is True:
            choice = "once"
        elif choice is False or choice not in ("once", "always"):
            choice = "deny"
        w = self._confirm_waiters.get(int(seq))
        if w:
            ev, box = w
            box["choice"] = choice
            ev.set()
        return {"ok": True}

    # ---------- 生命周期 ----------
    def on_closed(self):
        self._cancel.set()
        self._save_session()
        try:
            self.slog.close()
        except Exception:
            pass
        plugin_loader.shutdown()

    def run(self):
        self._session_tool_log = []
        self.window = webview.create_window(
            "EZcode · 安全运营研判工作台", os.path.join(BASE_DIR, "webui.html"),
            js_api=self, width=1240, height=860, min_size=(900, 620))
        self.window.events.closed += self.on_closed
        webview.start(debug=False)      # debug=True可开DevTools(F12)


def main():
    app = SecopGuiApp()
    try:
        app.run()
    except Exception as e:
        print("GUI启动失败(%s: %s)——请确认Windows已装WebView2运行时(Win10/11自带)。" % (type(e).__name__, e))
        print("或改用 TUI: python -X utf8 tui.py")
        input("按回车退出...")


if __name__ == "__main__":
    main()
