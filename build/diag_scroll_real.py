# -*- coding: utf-8 -*-
"""真实场景验证: 切走(失焦) → 后台持续推流 → 切回 → 是否自动贴到最新
   同时验证回来后在窗口内向上滚动是逐行、不跳段。
"""
import ctypes, json, os, sys, threading, time
from ctypes import wintypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "build"))
import diag_scroll as D   # 复用 MockApi / push_stream 等

u = ctypes.windll.user32
W = [None]
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print("  %s %s %s" % ("OK  " if ok else "FAIL", name,
                          ("-- " + str(detail)[:110]) if not ok else ""), flush=True)


def find_win():
    hits = []
    P = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        if u.IsWindowVisible(h):
            b = ctypes.create_unicode_buffer(300)
            u.GetWindowTextW(h, b, 300)
            if "滚动实测" in b.value:
                hits.append(h)
        return True

    u.EnumWindows(P(cb), 0)
    return hits[0] if hits else None


def js(code):
    return W[0].evaluate_js(code)


def para(n=8):
    return "这是一段用于撑高聊天区的测试文字，重复多次以产生足够滚动距离。" * n


def go():
    t0 = time.time()
    while time.time() - t0 < 25:
        try:
            if js("window.__ezcodeBooted === true"):
                break
        except Exception:
            pass
        time.sleep(0.25)
    time.sleep(0.6)

    try:
        full = ""
        for i in range(18):
            full += "第%d帧 %s\n\n" % (i, para())
            js("window.ezcodeBridge.event(%s)"
               % json.dumps({"t": "text", "text": full}, ensure_ascii=False))
            time.sleep(0.05)
        time.sleep(0.4)
        dist = js("$('#chat').scrollHeight - $('#chat').scrollTop - $('#chat').clientHeight")
        check("前台流式跟随到底", dist < 6, "dist=%s" % dist)

        # ---- 切走: 把焦点交给桌面, 窗口被别的应用盖住(不最小化) ----
        hwnd = find_win()
        u.ShowWindow(hwnd, 8)                    # SW_SHOWNA: 不动焦点
        shell = u.GetShellWindow()
        u.SetForegroundWindow(shell)
        time.sleep(0.8)
        check("窗口已失去焦点", u.GetForegroundWindow() != hwnd,
              "fg=%s hwnd=%s" % (u.GetForegroundWindow(), hwnd))

        # ---- 后台继续生成(此时用户在看别的工具) ----
        for i in range(18, 44):
            full += "第%d帧 %s\n\n" % (i, para())
            js("window.ezcodeBridge.event(%s)"
               % json.dumps({"t": "text", "text": full}, ensure_ascii=False))
            time.sleep(0.08)
        time.sleep(0.5)

        # ---- 切回 ----
        u.SetForegroundWindow(hwnd)
        time.sleep(0.9)
        dist = js("$('#chat').scrollHeight - $('#chat').scrollTop - $('#chat').clientHeight")
        check("切回后视口停在最新回答", dist < 6, "dist=%s" % dist)
        check("切回后不显示「回到最新」按钮",
              js("!$('#jump-bottom').classList.contains('show')"))

        # ---- 回来后再生成几帧, 仍应跟随 ----
        full += "回到前台后继续生成 " + para(4)
        js("window.ezcodeBridge.event(%s)"
           % json.dumps({"t": "text", "text": full}, ensure_ascii=False))
        time.sleep(0.4)
        dist = js("$('#chat').scrollHeight - $('#chat').scrollTop - $('#chat').clientHeight")
        check("切回后继续跟随", dist < 6, "dist=%s" % dist)

        # ---- 窗口内向上滚: 逐行不跳段 ----
        st0 = js("$('#chat').scrollTop")
        for _ in range(3):
            js("(function(){var c=$('#chat');"
               " c.dispatchEvent(new WheelEvent('wheel',{deltaY:-100,bubbles:true}));"
               " c.scrollTop = Math.max(0, c.scrollTop-100);"
               " c.dispatchEvent(new Event('scroll'));})()")
            time.sleep(0.12)
        st1 = js("$('#chat').scrollTop")
        check("上滚 3 格位移≈300px(逐行, 未跳段)", abs((st0 - st1) - 300) <= 30,
              "%s→%s" % (st0, st1))

        # 上滚期间继续生成: 视口不被拽走
        full += "上翻期间继续生成 " + para(6)
        js("window.ezcodeBridge.event(%s)"
           % json.dumps({"t": "text", "text": full}, ensure_ascii=False))
        time.sleep(0.5)
        st2 = js("$('#chat').scrollTop")
        dist = js("$('#chat').scrollHeight - $('#chat').scrollTop - $('#chat').clientHeight")
        check("上翻期间视口稳定(不被拽回底部)", abs(st2 - st1) <= 10,
              "st1=%s st2=%s" % (st1, st2))
        check("上翻期间新内容不覆盖当前阅读位置", dist > 100, "dist=%s" % dist)

    except Exception as e:
        check("执行异常", False, "%s: %s" % (type(e).__name__, e))
    finally:
        ok = sum(1 for _, o in results if o)
        print("\n真实场景: %d通过 / %d失败" % (ok, len(results) - ok), flush=True)
        os._exit(0 if ok == len(results) else 1)


import webview
W[0] = webview.create_window("EZcode 滚动实测",
                             os.path.join(ROOT, "app", "webui", "index.html"),
                             js_api=D.MockApi(), width=1000, height=760)
threading.Thread(target=go, daemon=True).start()
webview.start()
