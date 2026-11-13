# -*- coding: utf-8 -*-
"""安装器 / 卸载器共用: 快捷方式、注册表卸载项、路径解析
只依赖 pywin32 + 标准库, 全部操作在 HKCU 与用户目录, 不需要管理员权限。
"""
import ctypes
import os
import subprocess
import sys
import winreg

APP_NAME = "EZcode"
DISPLAY_NAME = "EZcode"
EXE_NAME = "EZcode.exe"
FOLDER_NAME = "EZcode"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\EZcode"
PUBLISHER = "EZcode"

# 版本号唯一事实源: 根目录 ezcode_version.py。构建脚本与安装器都从这里读,
# 不再各写一份; 读不到(极端的独立打包场景)才回退到内置值。
try:
    import ezcode_version as _V
    VERSION = _V.APP_VERSION
except Exception:
    try:
        import importlib.util
        _spec = importlib.util.spec_from_file_location(
            "ezcode_version", os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "ezcode_version.py"))
        _m = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_m)
        VERSION = _m.APP_VERSION
    except Exception:
        VERSION = "1.0.48"

CSIDL_DESKTOP = 0x0010
CSIDL_PROGRAMS = 0x0002
CSIDL_APPDATA = 0x001A
CSIDL_LOCAL_APPDATA = 0x001C


def known_folder(csidl):
    """用 Shell API 取真实路径(兼容 OneDrive 桌面重定向)"""
    buf = ctypes.create_unicode_buffer(260)
    try:
        ctypes.windll.shell32.SHGetFolderPathW(0, csidl, 0, 0, buf)
    except Exception:
        return ""
    return buf.value


def desktop_dir():
    return known_folder(CSIDL_DESKTOP)


def programs_dir():
    return known_folder(CSIDL_PROGRAMS)


def local_appdata():
    return known_folder(CSIDL_LOCAL_APPDATA)


def default_install_dir():
    base = local_appdata() or os.path.expanduser("~")
    return os.path.join(base, "Programs", FOLDER_NAME)


def shortcut(lnk_path, target, icon, args="", desc="", workdir=None):
    """创建 .lnk (纯动态 Dispatch, 不依赖 makepy 缓存)"""
    import win32com.client.dynamic
    os.makedirs(os.path.dirname(lnk_path), exist_ok=True)
    sh = win32com.client.dynamic.Dispatch("WScript.Shell")
    lnk = sh.CreateShortCut(lnk_path)
    lnk.TargetPath = target
    if args:
        lnk.Arguments = args
    lnk.WorkingDirectory = workdir or os.path.dirname(target)
    lnk.IconLocation = icon
    lnk.Description = desc
    lnk.Save()
    return lnk_path


def write_uninstall_entry(install_dir, uninstall_cmd):
    icon = os.path.join(install_dir, EXE_NAME)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_KEY, 0,
                            winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, DISPLAY_NAME)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, install_dir)
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, icon)
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, uninstall_cmd)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def remove_uninstall_entry():
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _silent_cmd(cmd):
    """无窗口执行命令(不会闪黑色 cmd)"""
    return subprocess.Popen(cmd, shell=True,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=0x08000000,   # CREATE_NO_WINDOW
                            close_fds=True)


def delayed_remove_tree(path):
    """卸载后延迟删除整个安装目录(含正在运行的 exe), 无窗口"""
    # ping 作延时(约2秒), 等本进程退出后 rmdir /s /q
    cmd = 'ping -n 3 127.0.0.1 >nul & rmdir /s /q "%s"' % path
    try:
        _silent_cmd(cmd)
    except Exception:
        pass


def install_icon():
    """打包后图标文件所在目录: 安装目录顶层"""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "EZcode.ico")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "EZcode.ico")
