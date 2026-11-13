# -*- coding: utf-8 -*-
"""启动耗时基准: 进程创建 → 顶层窗口可见 → WebView2 子窗口就绪。

用法: python bench_startup.py [次数]
全部动作在单进程内闭环(启动/轮询/回收), 避免外层 shell 结束时误杀 GUI。
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import tempfile
from ctypes import wintypes

EXE = os.environ.get("SECOP_EXE") or os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Programs", "EZcode", "EZcode.exe")
# 可选: 用 JSON 数组指定任意启动命令(例如源码 pythonw app/client.py)做对照
CMD = json.loads(os.environ["SECOP_CMD"]) if os.environ.get("SECOP_CMD") else [EXE]
CWD = os.environ.get("SECOP_CWD") or os.path.dirname(CMD[0])
INTERVAL = 0.015
TIMEOUT = 40.0

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindows = user32.EnumWindows
EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
EnumChildWindows = user32.EnumChildWindows
EnumChildWindows.argtypes = [wintypes.HWND, ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
GetWindowTextW = user32.GetWindowTextW
GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
GetClassNameW = user32.GetClassNameW
GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
IsWindowVisible = user32.IsWindowVisible
IsWindowVisible.argtypes = [wintypes.HWND]
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def _text(hwnd):
    b = ctypes.create_unicode_buffer(512)
    GetWindowTextW(hwnd, b, 512)
    return b.value


def _cls(hwnd):
    b = ctypes.create_unicode_buffer(256)
    GetClassNameW(hwnd, b, 256)
    return b.value


def _find(pid):
    """只认本次启动 PID 拥有的窗口, 避免抓到后台残留实例。"""
    found = [None, False]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not IsWindowVisible(hwnd):
            return True
        owner = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        t = _text(hwnd)
        if "ezcode" in t.lower() and "工作台" in t:
            found[0] = hwnd

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def child(ch, _):
                if _cls(ch).startswith("Chrome_"):
                    found[1] = True
                    return False
                return True

            EnumChildWindows(hwnd, child, 0)
        return True

    EnumWindows(each, 0)
    return found[0], found[1]


def _kill(p):
    try:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=0x08000000)
    except Exception:
        try:
            p.terminate()
        except Exception:
            pass


def bench(n=1):
    if not os.path.isfile(CMD[0]):
        print("找不到启动程序:", CMD[0])
        return
    rows = []
    for i in range(n):
        # 确保上一轮全部退出, 避免单实例/端口干扰
        time.sleep(0.6)
        t0 = time.perf_counter()
        prof = os.path.join(tempfile.gettempdir(),
                            "ezcode-boot-%d-%d.txt" % (int(t0 * 1000) % 100000000, i))
        env = dict(os.environ)
        env["SECOP_BOOT_PROFILE"] = prof
        p = subprocess.Popen(CMD, cwd=CWD,
                             env=env,
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=0x08000000, close_fds=True)
        t_win = t_web = t_boot = None
        while time.perf_counter() - t0 < TIMEOUT:
            hwnd, web = _find(p.pid)
            if hwnd and t_win is None:
                t_win = time.perf_counter() - t0
            if hwnd and web and t_web is None:
                t_web = time.perf_counter() - t0
            if t_boot is None and os.path.isfile(prof):
                try:
                    with open(prof, encoding="utf-8") as f:
                        txt = f.read()
                        if "frontend_rendered" in txt or "bootstrap_done" in txt:
                            t_boot = time.perf_counter() - t0
                except Exception:
                    pass
            if t_web and t_boot:
                break
            time.sleep(INTERVAL)
        _kill(p)
        stages = ""
        if os.path.isfile(prof):
            try:
                with open(prof, encoding="utf-8") as f:
                    stages = " | ".join(f.read().strip().splitlines())
            except Exception:
                pass
        rows.append((t_win, t_web, t_boot))
        print("第%d次: 窗口 %s | 渲染 %s | 可操作 %s" % (
            i + 1,
            "%.2fs" % t_win if t_win else "超时",
            "%.2fs" % t_web if t_web else "超时",
            "%.2fs" % t_boot if t_boot else "无打点"))
        if stages:
            print("       阶段(相对 client 加载): " + stages)
    ok = [r for r in rows if r[0] and r[1]]
    if ok:
        print("平均: 窗口 %.2fs / 渲染 %.2fs" % (
            sum(r[0] for r in ok) / len(ok),
            sum(r[1] for r in ok) / len(ok)))
    boot = [r[2] for r in rows if r[2]]
    if boot:
        print("平均可操作: %.2fs" % (sum(boot) / len(boot)))


if __name__ == "__main__":
    bench(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
