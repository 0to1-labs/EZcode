# -*- coding: utf-8 -*-
"""EZcode 桌面客户端 · 无控制台启动器(项目内规范入口)

优先拉起已安装的 EZcode.exe(最快); 未安装时回退为 pythonw 直接跑源码。
全程不出现黑色 cmd 会话框。排障请改用 debug.bat(带控制台, 可见 traceback)。
不写死任何用户目录, 从当前解释器推导 pythonw.exe。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(ROOT, "app", "client.py")

PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
if not os.path.isfile(PYW):
    PYW = shutil.which("pythonw") or shutil.which("pyw") or sys.executable

EXE = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                   "Programs", "EZcode", "EZcode.exe")


def alert(msg):
    """无控制台环境下用系统弹窗报告启动失败。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(msg), "EZcode · 启动失败", 0x10)
    except Exception:
        pass


def main():
    if os.path.isfile(EXE):
        target, cwd = [EXE], os.path.dirname(EXE)
    elif os.path.isfile(CLIENT) and os.path.isfile(PYW):
        target, cwd = [PYW, "-X", "utf8", CLIENT], ROOT
    else:
        alert("找不到客户端文件:\n" + CLIENT +
              "\n也找不到已安装的 EZcode.exe。")
        return
    try:
        subprocess.Popen(
            target, cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,      # CREATE_NO_WINDOW
            close_fds=True,
        )
    except Exception as e:
        alert("启动失败: %s: %s" % (type(e).__name__, e))


main()
