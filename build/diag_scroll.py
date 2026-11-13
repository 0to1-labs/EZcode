# -*- coding: utf-8 -*-
"""滚动跟随修复的真实窗口验证。
mock js_api(不依赖真实LLM/引擎), 加载真实 index.html, 用事件桥模拟流式输出,
再在真实 DOM 上模拟"用户上滚一格"验证不被吸回、跳底按钮、回底恢复。
用法: python diag_scroll.py
"""
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
WEBUI = os.path.join(APP, "webui")
sys.path.insert(0, APP)

STATE = {"mode": "query", "model": "mock", "active": "mock", "busy": False,
         "sid": "s1", "title": "滚动测试", "ctx_pct": 1, "n_msgs": 0,
         "persona": "ezcode", "persona_label": "安全运营", "profiles": {}}


class MockApi:
    """前端调用的全部js_api(17个)都给最小实现, 防 is not a function。"""
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
    def set_mode(self, m): return {"ok": True}
    def set_model(self, k): return {"ok": True}
    def set_persona(self, p): return {"ok": True}
    def llm_config(self): return {"path": "", "active": "", "profiles": {}}
    def save_llm_config(self, *a): return {"ok": True}
    def export_session(self): return ""


class Runner:
    def __init__(self):
        self.win = None
        self.results = []

    def check(self, name, ok, detail=""):
        self.results.append((name, ok, detail))
        print("  %s %s %s" % ("✓" if ok else "✗", name, ("-- " + str(detail)[:120]) if not ok else ""))

    def js(self, code):
        r = self.win.evaluate_js(code)
        return r

    def push_stream(self, text):
        """模拟一帧流式正文。"""
        payload = json.dumps({"t": "text", "text": text}, ensure_ascii=False)
        return self.js("window.ezcodeBridge.event(%s)" % payload)

    def run(self):
        import webview
        self.win = webview.create_window(
            "滚动诊断", os.path.join(WEBUI, "index.html"),
            js_api=MockApi(), width=1000, height=700, min_size=(600, 400))
        threading.Thread(target=self._scenario, daemon=True).start()
        webview.start()

    def _scenario(self):
        # 0. 等前端boot完成
        t0 = time.time()
        while time.time() - t0 < 15:
            if self.js("window.__ezcodeBooted === true"):
                break
            time.sleep(0.2)
        time.sleep(0.5)
        print("== 0. 启动 ==")
        self.check("前端boot完成", self.js("window.__ezcodeBooted === true"))

        # 1. 灌入长内容并模拟流式跟随
        print("== 1. 流式跟随 ==")
        para = ("这是一段用于撑高聊天区的测试文字，重复多次以产生足够滚动距离。" * 8)
        full = ""
        for i in range(30):
            full += "第%d帧 %s\n\n" % (i, para)
            self.push_stream(full)
            time.sleep(0.05)
        self.js("window.ezcodeBridge.event(%s)"
                % json.dumps({"t": "round_end", "dt": 1, "n_tools": 0}, ensure_ascii=False))
        time.sleep(0.4)
        m = self.js("JSON.stringify({st: $('#chat').scrollTop, dist: $('#chat').scrollHeight - $('#chat').scrollTop - $('#chat').clientHeight})")
        d = json.loads(m or "{}")
        self.check("流式期间自动跟随到底", d.get("dist", 999) < 5, m)

        # 2a. 模拟用户上滚一格(旧bug场景: 被吸回/滚不动)
        print("== 2. 上滚一格 ==")
        before = self.js("$('#chat').scrollTop")
        # 真实用户上滚: 必须带 wheel 事件(v39 起判据是真实输入, 不再靠 scrollTop 差值猜)
        self.js("(function(){var c=$('#chat');"
                " c.dispatchEvent(new WheelEvent('wheel',{deltaY:-100,bubbles:true}));"
                " c.scrollTop = Math.max(0, c.scrollTop-100);"
                " c.dispatchEvent(new Event('scroll'));})()")
        time.sleep(0.15)
        full += "用户上滚后继续生成的新内容帧 " + para * 3
        self.push_stream(full)
        time.sleep(0.3)
        st_after = self.js("$('#chat').scrollTop")
        dist = self.js("$('#chat').scrollHeight - $('#chat').scrollTop - $('#chat').clientHeight")
        self.check("上滚后autoScroll关闭", self.js("ui.autoScroll === false"))
        self.check("新帧不吸回(保持用户视口)", abs(st_after - (before - 100)) <= 12,
                   "上滚后=%s 新帧后=%s dist=%s" % (before - 100, st_after, dist))
        self.check("视口确实离开了底部", st_after < before + 50,
                   "before=%s after=%s" % (before, st_after))

        # 2b. 用户向下滚(接管视口, 顺着滚动走, 不被瞬移)
        print("== 3. 向下滚(视口接管) ==")
        st_before = self.js("$('#chat').scrollTop")
        self.js("(function(){var c=$('#chat');"
                " c.dispatchEvent(new WheelEvent('wheel',{deltaY:250,bubbles:true}));"
                " c.scrollTop = Math.min(c.scrollHeight-c.clientHeight, c.scrollTop+250);})()")
        time.sleep(0.15)
        st_moved = self.js("$('#chat').scrollTop")
        self.check("向下滚后跟随暂停(用户接管)", self.js("ui.autoScroll === false"))
        self.check("视口顺着滚动移动", st_moved > st_before + 100, "%s→%s" % (st_before, st_moved))
        full += "向下滚动后继续生成的新内容帧 " + para * 2
        self.push_stream(full)
        time.sleep(0.3)
        st_keep = self.js("$('#chat').scrollTop")
        dist = self.js("$('#chat').scrollHeight - $('#chat').scrollTop - $('#chat').clientHeight")
        self.check("向下滚后新帧不瞬移到底(视口保留)", st_keep < st_moved + 60 and dist > 150,
                   "moved=%s keep=%s dist=%s" % (st_moved, st_keep, dist))

        # 4. 跳底按钮 → 向上滚 → 按钮 → 向下滚回底部附近恢复跟随
        print("== 5. 跳底与滚回恢复 ==")
        self.js("$('#jump-bottom').click()")
        time.sleep(0.3)
        dist = self.js("$('#chat').scrollHeight - $('#chat').scrollTop - $('#chat').clientHeight")
        self.check("点击按钮回到底部", dist < 5, "dist=%s" % dist)
        self.check("点击后恢复跟随", self.js("ui.autoScroll === true"))
        full += "恢复跟随后的新帧A " + para
        self.push_stream(full)
        time.sleep(0.25)
        dist = self.js("$('#chat').scrollHeight - $('#chat').scrollTop - $('#chat').clientHeight")
        self.check("恢复后粘底跟随平滑", dist < 5, "dist=%s" % dist)
        # 向上滚(远离底部) → 再向下滚回底部附近 → 恢复
        self.js("(function(){var c=$('#chat');"
                " c.dispatchEvent(new WheelEvent('wheel',{deltaY:-600,bubbles:true}));"
                " c.scrollTop = 0;})()")
        time.sleep(0.15)
        self.check("上滚后按钮再现", self.js("$('#jump-bottom').classList.contains('show') === true"))
        self.check("上滚后跟随暂停", self.js("ui.autoScroll === false"))
        self.js("(function(){var c=$('#chat');"
                " c.dispatchEvent(new WheelEvent('wheel',{deltaY:600,bubbles:true}));"
                " c.scrollTop = c.scrollHeight - c.clientHeight - 4;"
                " c.dispatchEvent(new Event('scroll'));})()")
        time.sleep(0.15)
        self.check("向下滚到真正的底部才恢复跟随", self.js("ui.autoScroll === true"))
        full += "恢复跟随后的新帧B " + para * 2
        self.push_stream(full)
        time.sleep(0.3)
        dist = self.js("$('#chat').scrollHeight - $('#chat').scrollTop - $('#chat').clientHeight")
        self.check("恢复后新帧继续跟随", dist < 5, "dist=%s" % dist)

        # 6. 切到别的窗口再回来: 内容长了, 视口仍应停在最新(用户报告的核心场景)
        print("== 6. 切走再回来(跟随不中断) ==")
        self.js("$('#jump-bottom').click()")
        time.sleep(0.25)
        # 模拟在后台期间内容增长 + 浏览器恢复时布局变化引起的 scrollTop 变化(无 wheel 输入)
        full += "后台期间持续生成的内容 " + para * 4
        self.push_stream(full)
        time.sleep(0.3)
        self.js("(function(){var c=$('#chat'); c.scrollTop = Math.max(0, c.scrollTop-260);})()")  # 触发 scroll, 无输入
        time.sleep(0.25)
        self.check("无输入的 scrollTop 变化不影响跟随态", self.js("ui.autoScroll === true"))
        self.js("document.dispatchEvent(new Event('visibilitychange'))")
        time.sleep(0.25)
        dist = self.js("$('#chat').scrollHeight - $('#chat').scrollTop - $('#chat').clientHeight")
        self.check("切回窗口后仍贴最新", dist < 5, "dist=%s" % dist)
        self.check("切回后不显示「回到最新」", self.js("!$('#jump-bottom').classList.contains('show')"))

        # 7. 上滚一次只走一格, 不跳段
        print("== 7. 滚轮逐行上翻(不跳段) ==")
        st0 = self.js("$('#chat').scrollTop")
        self.js("(function(){var c=$('#chat');"
                " c.dispatchEvent(new WheelEvent('wheel',{deltaY:-100,bubbles:true}));"
                " c.scrollTop = Math.max(0, c.scrollTop-100);"
                " c.dispatchEvent(new Event('scroll'));})()")
        time.sleep(0.2)
        st1 = self.js("$('#chat').scrollTop")
        self.check("上滚一格位移≈100px(未跳段)", abs((st0 - st1) - 100) <= 8,
                   "%s→%s" % (st0, st1))
        full += "上翻期间继续生成 " + para
        self.push_stream(full)
        time.sleep(0.3)
        st2 = self.js("$('#chat').scrollTop")
        dist = self.js("$('#chat').scrollHeight - $('#chat').scrollTop - $('#chat').clientHeight")
        self.check("上翻期间视口不被拽回底部", abs(st2 - st1) <= 8,
                   "st1=%s st2=%s dist=%s" % (st1, st2, dist))
        self.check("上翻期间内容仍可继续读(未跳段)", self.js("ui.autoScroll === false"))

        # 8. 底部小幅上滚(仍在旧容差 70px 内) —— 曾把视口拽回底部的根因场景
        print("== 8. 底部小幅上滚不被拽回 ==")
        self.js("$('#jump-bottom').click()")
        time.sleep(0.3)
        before_small = self.js("$('#chat').scrollTop")
        self.js("(function(){var c=$('#chat');"
                " c.dispatchEvent(new WheelEvent('wheel',{deltaY:-50,bubbles:true}));"
                " c.scrollTop = Math.max(0, c.scrollTop-50);"
                " c.dispatchEvent(new Event('scroll'));})()")
        time.sleep(0.2)
        self.check("上滚50px立刻停止跟随(不再等70px容差)",
                   self.js("ui.autoScroll === false"))
        full += "小幅上滚后继续生成的内容 " + para * 2
        self.push_stream(full)
        time.sleep(0.35)
        keep_small = self.js("$('#chat').scrollTop")
        self.check("上滚50px后新帧不把视口拽回底部",
                   abs(keep_small - (before_small - 50)) <= 14,
                   "上滚后=%s 新帧后=%s" % (before_small - 50, keep_small))
        self.check("跟随保持关闭(保护期内)",
                   self.js("ui.autoScroll === false"))

        # 9. 思考面板: 结束不强制折叠, 用户折叠/展开时视口不动
        print("== 9. 思考面板(不强制折叠/视口稳定) ==")
        self.js("(function(){var s=document.querySelector('#chat .stream'); if(s) s.innerHTML='';"
                " var w=document.querySelector('#welcome'); if(w) w.classList.remove('show');})()")
        for i in range(6):
            self.js("addUser('问题%d')" % i)
            self.js("addBotFull(%s)" % json.dumps("历史内容 " + para * 3, ensure_ascii=False))
        time.sleep(0.3)
        self.js("addUser('看这条告警')")
        self.js("window.ezcodeBridge.event(%s)" % json.dumps(
            {"t": "think", "text": "先看要素", "chars": 4}, ensure_ascii=False))
        time.sleep(0.3)
        self.js("window.ezcodeBridge.event(%s)" % json.dumps(
            {"t": "think_end", "chars": 400}, ensure_ascii=False))
        time.sleep(0.35)
        self.check("思考结束后面板不被程序折叠",
                   self.js("(function(){var t=document.querySelector('.think');"
                           "return t ? t.open === true : false;})()"))
        self.js("$('#jump-bottom').click()")
        time.sleep(0.25)
        self.js("(function(){var c=$('#chat');"
                " c.dispatchEvent(new WheelEvent('wheel',{deltaY:-120,bubbles:true}));"
                " c.scrollTop = Math.max(0, c.scrollTop-120);"
                " c.dispatchEvent(new Event('scroll'));})()")
        time.sleep(0.2)
        top_before_fold = self.js("$('#chat').scrollTop")
        self.js("document.querySelector('.think summary').click()")
        time.sleep(0.35)
        top_after_fold = self.js("$('#chat').scrollTop")
        self.check("折叠思考面板时视口钉在原处",
                   abs(top_after_fold - top_before_fold) <= 14,
                   "%s → %s" % (top_before_fold, top_after_fold))

        n_ok = sum(1 for _, ok, _ in self.results if ok)
        print()
        print("滚动诊断: %d通过 / %d失败" % (n_ok, len(self.results) - n_ok))
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0 if n_ok == len(self.results) else 1)


if __name__ == "__main__":
    Runner().run()
