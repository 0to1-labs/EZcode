# -*- coding: utf-8 -*-
"""生成单文件安装程序 EZcodeSetup.exe (内嵌程序 payload + 图标 + 向导)"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ICO = os.path.join(HERE, "EZcode.ico")
PNG = os.path.join(HERE, "EZcode-icon.png")
PAYLOAD = os.path.join(HERE, "ezcode-payload.zip")
VER = os.path.join(HERE, "version_setup.txt")
DIST = os.path.join(HERE, "installer-dist-fast")
WORK = os.path.join(HERE, "installer-work-fast")
SPEC = os.path.join(HERE, "spec-setup-fast")
os.makedirs(SPEC, exist_ok=True)

for f in (ICO, PNG, PAYLOAD, VER):
    if not os.path.exists(f):
        raise SystemExit("缺少文件: %s (先执行 make_icon.py / build_app.py / pack_payload.py)" % f)

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm",
    "--onefile", "--windowed",
    "--name", "EZcodeSetup",
    "--distpath", DIST, "--workpath", WORK, "--specpath", SPEC,
    "--icon", ICO, "--version-file", VER,
    "--paths", HERE, "--paths", ROOT,
    "--hidden-import", "ezcode_common",
    "--hidden-import", "ezcode_version",
    "--hidden-import", "win32com.client.dynamic",
    "--hidden-import", "tkinter.filedialog",
    "--add-data", "%s;." % PAYLOAD,
    "--add-data", "%s;." % ICO,
    "--add-data", "%s;." % PNG,
    os.path.join(HERE, "installer.py"),
]
print("开始打包安装程序...\n")
r = subprocess.run(cmd, cwd=HERE)
if r.returncode != 0:
    raise SystemExit("安装器打包失败, code=%s" % r.returncode)

out = os.path.join(DIST, "EZcodeSetup.exe")
print("\n安装程序:", out)
print("大小: %.1f MB" % (os.path.getsize(out) / 1024 / 1024))
