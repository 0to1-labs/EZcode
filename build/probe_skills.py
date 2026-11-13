# -*- coding: utf-8 -*-
"""探针: 切skills tab, 捕获window.onerror的JS异常与面板内容。"""
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)

SKILLS = []
RAW = json.load(open(os.path.join(ROOT, "devices.json"), encoding="utf-8"))
STATE = {"mode": "query", "model": "m", "active": "m", "busy": False, "sid": "s", "title": "t",
         "ctx_pct": 1, "n_msgs": 0, "persona": "ezcode", "persona_label": "安全运营", "profiles": {}}


class MockApi:
    def js_ready(self): return {"ok": True}
    def bootstrap(self): return {"ok": True, "state": STATE, "history": [], "sessions": []}
    def boot_done(self): return {"ok": True}
    def state(self): return dict(STATE)
    def history(self): return []
    def list_sessions(self): return []
    def send(self, t): return {"ok": True}
    def stop(self): return {"ok": True}
    def confirm_result(self, *a): return {"ok": True}
    def new_session(self): return {"ok": True}
    def open_session(self, s): return {"ok": True}
    def delete_session(self, s): return {"ok": True}
    def set_mode(self, m): return {"ok": True}
    def set_model(self, k): return {"ok": True}
    def set_persona(self, p): return {"ok": True}
    def llm_config(self): return {"path": "", "active": "", "profiles": {}}
    def save_llm_config(self, *a): return {"ok": True}
    def export_session(self): return ""
    def settings(self):
        out = {"path": r"C:\x\devices.json"}
        for k in ("llm", "threat_intel", "netbox", "edr", "ngsoc",
                  "rag", "custom_api", "mcp", "builtin_tools"):
            out[k] = RAW.get(k)
        return out
    def save_settings(self, s): return {"ok": True, "note": "mock"}
    def mcp_test(self, spec): return {"ok": True, "n_tools": 0, "tools": []}
    def skills_list(self):
        return {"skills": list(SKILLS), "global_dir": "g", "project_dir": "p"}
    def skills_save(self, *a): return {"ok": True}
    def skills_delete(self, *a): return {"ok": True}
    def plugins_list(self): return {"plugins": [], "dir": "d"}
    def plugin_install(self, *a): return {"ok": True}
    def plugin_set_enabled(self, *a): return {"ok": True}
    def plugin_delete(self, *a): return {"ok": True}
    def pick_plugin_file(self): return {"ok": False, "error": "未选择"}


class R:
    def run(self):
        import webview
        self.win = webview.create_window("探针", os.path.join(APP, "webui", "index.html"),
                                         js_api=MockApi(), width=1000, height=700)
        threading.Thread(target=self.go, daemon=True).start()
        webview.start()

    def go(self):
        t0 = time.time()
        while time.time() - t0 < 12:
            if self.win.evaluate_js("window.__ezcodeBooted === true"):
                break
            time.sleep(0.2)
        time.sleep(0.3)
        self.win.evaluate_js("window.__errs=[]; window.onerror=function(m){window.__errs.push(String(m))};")
        # 打开设置→切技能tab
        self.win.evaluate_js("document.querySelector('#btn-settings').click()")
        time.sleep(0.6)
        self.win.evaluate_js(
            "document.querySelectorAll('.set-tab')[[...document.querySelectorAll('.set-tab')]"
            ".findIndex(b=>b.dataset.tab==='skills')].click()")
        time.sleep(2.0)
        errs = self.win.evaluate_js("JSON.stringify(window.__errs||[])")
        panel = self.win.evaluate_js("document.querySelector('#set-panel') && document.querySelector('#set-panel').innerHTML.slice(0,300)")
        tab = self.win.evaluate_js("_setTab")
        print("当前tab:", tab)
        print("JS异常:", errs)
        print("面板内容:", str(panel)[:300])
        sys.stdout.flush()
        os._exit(0)


R().run()
