# -*- coding: utf-8 -*-
"""卸载逻辑: 由主程序 EZcode.exe --uninstall 触发(无需额外 exe)"""
import ctypes
import os
import shutil
import sys

import ezcode_common as C


def _confirm():
    try:
        r = ctypes.windll.user32.MessageBoxW(
            0, "确定要卸载 EZcode 吗？\n\n"
               "会话记录与日志会一并删除，程序目录将在数秒后自动清理。",
            "卸载 EZcode", 0x24)          # MB_YESNO | MB_ICONQUESTION
        return r == 6
    except Exception:
        return True


def run():
    if not _confirm():
        return
    install_dir = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
                   else C.default_install_dir())

    # 1. 快捷方式
    removed = []
    d = C.desktop_dir()
    if d:
        p = os.path.join(d, "EZcode.lnk")
        if os.path.exists(p):
            try:
                os.remove(p)
                removed.append(p)
            except Exception:
                pass
    pdir = os.path.join(C.programs_dir(), C.FOLDER_NAME)
    if os.path.isdir(pdir):
        try:
            shutil.rmtree(pdir)
            removed.append(pdir)
        except Exception:
            pass

    # 2. 注册表卸载项
    C.remove_uninstall_entry()

    # 3. 延迟清理程序目录(含正在运行的自身)
    C.delayed_remove_tree(install_dir)

    try:
        ctypes.windll.user32.MessageBoxW(
            0, "EZcode 已卸载。\n\n程序目录将在几秒后自动清理完成。",
            "卸载完成", 0x40)             # MB_ICONINFORMATION
    except Exception:
        pass
