# -*- coding: utf-8 -*-
"""任务完成提示回归：文案清洗 / 触发策略 / 接线 / 真实窗口。

运行: python build/diag_notify.py
"""
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, ROOT)

import notify as N                       # noqa: E402

PASS = FAIL = 0


def ck(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % name)
    else:
        FAIL += 1
        print("  ✗ %s   %s" % (name, detail))


class FakeEngine:
    def __init__(self, sid="A", lang="zh"):
        self.sid = sid
        self.lang = lang


class FakeApp:
    def __init__(self, sid="A"):
        self.engine = FakeEngine(sid)
        self.window = None


def logic():
    print("=== 一、文案清洗 ===")
    s = N._clean("## 结论\n\n**已处置** 37 条 `告警`，| 表格 |\n\n建议：先隔离。")
    ck("去掉 markdown 标记", "#" not in s and "**" not in s and "`" not in s, s)
    ck("折叠空白与换行", "\n" not in s and "  " not in s)
    ck("保留正文", "已处置" in s and "建议" in s)
    long = N._clean("字" * 400)
    ck("超长截断并加省略号", long.endswith("…") and len(long) <= 151, len(long))
    ck("空值不炸", N._clean(None) == "")

    print("=== 二、触发策略 ===")
    n = N.Notifier(FakeApp("A"))
    ck("off 一律不弹", n.allowed("off", "A") is False)
    ck("always 一律弹", n.allowed("always", "A") is True)
    n.main_in_foreground = lambda: True          # 正盯着主窗
    ck("auto + 前台 + 同一任务 → 不打扰", n.allowed("auto", "A") is False)
    ck("auto + 前台但切到别的任务 → 提醒", n.allowed("auto", "B") is True)
    n.main_in_foreground = lambda: False
    ck("auto + 窗口不在前台 → 提醒", n.allowed("auto", "A") is True)
    n2 = N.Notifier(FakeApp("A"))
    n2.main_in_foreground = lambda: True
    ck("未知模式按 auto 处理", n2.allowed("", "A") is False)

    print("=== 三、偏好解析 ===")
    from engine import _ui_notify
    ck("默认 auto", _ui_notify({}) == "auto")
    ck("读取 ui.notify", _ui_notify({"ui": {"notify": "off"}}) == "off")
    ck("非法值回落 auto", _ui_notify({"ui": {"notify": "xx"}}) == "auto")
    ck("大小写兼容", _ui_notify({"ui": {"notify": "ALWAYS"}}) == "always")

    print("=== 四、接线 ===")
    eng = open(os.path.join(ROOT, "app", "engine.py"), encoding="utf-8").read()
    ck("round_end 带任务标题", '"title": self.sess.get("title")' in eng)
    ck("round_end 带专家图标", '"persona_icon"' in eng)
    ck("state 返回 notify 偏好", '"notify": _ui_notify(self.cfg)' in eng)
    ck("引擎有 set_notify", "def set_notify(self, mode)" in eng)
    cli = open(os.path.join(ROOT, "app", "client.py"), encoding="utf-8").read()
    ck("桥接注册 set_notify", '"set_notify"' in cli)
    ck("完成事件触发提示", "_notify_done(payload)" in cli)
    ck("只在未取消时提示", 'not payload.get("cancelled")' in cli)
    ck("提示异常不影响主流程", "[notify] 完成提示失败" in cli)
    js = open(os.path.join(ROOT, "app", "webui", "app.js"), encoding="utf-8").read()
    ck("偏好面板有完成提示", 'seg("notify"' in js)
    ck("点击卡片可回到任务", "openSessionFromNotify" in js)
    bld = open(os.path.join(ROOT, "build", "build_app.py"), encoding="utf-8").read()
    ck("打包隐藏导入 notify", '"notify"' in bld)

    print("=== 四b、完成回调透传 ===")
    from client import Client

    class StubNotify:
        def __init__(self):
            self.calls = []
            self.modes = []

        def allowed(self, mode, sid):
            self.modes.append((mode, sid))
            return mode != "off"

        def notify(self, **kw):
            self.calls.append(kw)

    class FakeEngCfg:
        def __init__(self, mode):
            self.cfg = {"ui": {"notify": mode}}
            self.sid = "A"
            self.lang = "zh"

    def make_client(mode):
        class C:
            def __init__(self):
                self.engine = FakeEngCfg(mode)
                self._notifier = StubNotify()

            def _notifier_obj(self):
                return self._notifier
        return C()

    c1 = make_client("auto")
    Client._notify_done(c1, {"t": "round_end", "sid": "A", "answer": "已分诊 37 条",
                             "title": "任务X", "persona_icon": "🛡", "dt": 3.2, "n_tools": 2})
    ck("把标题/图标/耗时/工具数传下去", bool(c1._notifier.calls)
       and c1._notifier.calls[0]["title"] == "任务X"
       and c1._notifier.calls[0]["icon"] == "🛡"
       and c1._notifier.calls[0]["dt"] == 3.2
       and c1._notifier.calls[0]["tools"] == 2,
       str(c1._notifier.calls)[:100])
    ck("sid 原样传给提示(点击可回到该任务)",
       c1._notifier.calls[0]["sid"] == "A")
    c2 = make_client("off")
    Client._notify_done(c2, {"t": "round_end", "sid": "A", "answer": "x"})
    ck("off 模式下不弹", not c2._notifier.calls)
    c3 = make_client("auto")
    Client._notify_done(c3, {"t": "round_end", "sid": "A", "answer": ""})
    ck("空回答有兜底文案", bool(c3._notifier.calls) and "已完成" in c3._notifier.calls[0]["body"],
       str(c3._notifier.calls)[:80])


class Shot:
    """真实窗口: 弹一条, 断言位置/尺寸/内容, 然后关掉。"""

    def __init__(self):
        self.results = N = []
        self.win = None

    def js(self, code, win=None):
        return (win or self.win).evaluate_js(code)

    def run(self):
        import webview
        self.win = webview.create_window("diag-main", html="<body></body>",
                                         width=420, height=300, hidden=True)
        threading.Thread(target=self._scenario, daemon=True).start()
        webview.start()

    def _scenario(self):
        import ctypes
        from ctypes import wintypes
        import win32gui
        t0 = time.time()
        while time.time() - t0 < 10:
            if self.js("document.readyState") == "complete":
                break
            time.sleep(0.2)
        time.sleep(0.4)
        print("\n=== 五、真实窗口 ===")
        app = FakeApp("A")
        app.engine.lang = "zh"
        n = N.Notifier(app)
        n.main_in_foreground = lambda: False
        n.notify(sid="A", title="研判今日高危告警", icon="🛡",
                 body="已分诊 37 条，6 条需立即处置。", dt=12.5, tools=4, life=15000)

        hwnd = 0
        t1 = time.time()
        while time.time() - t1 < 12:
            hits = []

            def cb(h, _):
                if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == "EZcode 提示":
                    hits.append(h)
                return True
            win32gui.EnumWindows(cb, None)
            if hits:
                hwnd = hits[0]
                break
            time.sleep(0.2)
        ck("提示窗已弹出且可见", bool(hwnd))
        # 等页面把卡片画出来(窗口可见早于 pushNote 完成)
        target = None
        for _ in range(60):
            for w in __import__("webview").windows:
                if getattr(w, "title", "") == "EZcode 提示":
                    target = w
                    break
            if target is not None:
                try:
                    if target.evaluate_js("!!document.querySelector('.tt')"):
                        break
                except Exception:
                    pass
            time.sleep(0.1)
        ck("能拿到提示窗对象", target is not None)

        if hwnd and target is not None:
            l, tp, r, b = win32gui.GetWindowRect(hwnd)
            ck("窗口逻辑尺寸=396x124", target.width == N.W and target.height == N.PAD * 2 + N.CARD_H,
               "%sx%s" % (target.width, target.height))
            # GetWindowRect 是物理像素, pywebview 用的是逻辑像素: 用两者比值换算
            scale = (r - l) / float(target.width or 1)
            ck("渲染无撑大(物理/逻辑一致)", abs(scale - round(scale)) < 0.02,
               "scale=%.3f rect=%s" % (scale, (l, tp, r, b)))
            x, bottom = N._workarea()
            ck("贴在主屏右下角(避开任务栏)", abs(r / scale - (x - 14)) <= 2
               and abs(b / scale - (bottom - 14)) <= 2,
               "rect=%s work=%s scale=%.2f" % ((l, tp, r, b), (x, bottom), scale))
            ck("置顶显示", bool(win32gui.GetWindowLong(hwnd, -20) & 0x8))
            ck("无标题栏", (win32gui.GetWindowLong(hwnd, -16) & 0x00C00000) == 0)
            ck("显示时不抢焦点", bool(win32gui.GetWindowLong(hwnd, -20) & 0x08000000))

        if target is not None:
            txt = target.evaluate_js(
                "(function(){var t=document.querySelector('.tt');"
                "var b=document.querySelector('.b');var m=document.querySelector('.meta');"
                "return t? t.textContent+'|'+(b?b.textContent:'')+'|'+(m?m.textContent:'') : ''})()")
            ck("标题渲染", "研判今日高危告警" in str(txt), str(txt)[:80])
            ck("正文渲染", "已分诊 37 条" in str(txt), str(txt)[:80])
            ck("元信息含用时与工具数", "12.5" in str(txt) and "4" in str(txt), str(txt)[:80])
            ck("卡片元素正确", target.evaluate_js(
                "!!document.querySelector('.n .ic') && !!document.querySelector('.n .bar')"))
            ck("底部进度条在跑", target.evaluate_js(
                "getComputedStyle(document.querySelector('.bar')).animationName === 'bar'"))
            target.evaluate_js("document.querySelector('.n').click()")
            time.sleep(0.6)
            ck("点击后提示窗关闭", not any(getattr(w, "title", "") == "EZcode 提示"
                                          for w in __import__("webview").windows))

        ok = PASS
        bad = FAIL
        print("\n完成提示诊断: %d通过 / %d失败" % (ok, bad))
        sys.stdout.flush()
        os._exit(0 if bad == 0 else 1)


if __name__ == "__main__":
    logic()
    Shot().run()
