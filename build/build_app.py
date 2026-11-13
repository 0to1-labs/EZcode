# -*- coding: utf-8 -*-
"""把 EZcode 客户端打成真正独立的 Windows exe (PyInstaller onedir/windowed)

关键设计: 所有运行时资源按「源码相对结构」放进 _MEIPASS, 这样
client/engine/store/tools 里基于 __file__ 的 ROOT/BASE_DIR 全部无需改动。
安装到 %LOCALAPPDATA%\\Programs\\EZcode 后目录可写, 日志/会话/判据库正常落盘。
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
# 输出到全新目录, 不做任何清理/删除(避免触碰已存在的构建产物与运行中的旧实例)
DIST = os.path.join(HERE, "dist-fast")
WORK = os.path.join(HERE, "work-fast")
SPEC = os.path.join(HERE, "spec-fast")
os.makedirs(SPEC, exist_ok=True)
ICO = os.path.join(HERE, "EZcode.ico")
VER = os.path.join(HERE, "version_info.txt")

try:
    import pythonnet
    PYNET_HOOKS = os.path.join(os.path.dirname(pythonnet.__file__), "_pyinstaller")
except Exception:
    PYNET_HOOKS = HERE

for f in (ICO, VER, os.path.join(APP, "bootstrap.py")):
    if not os.path.exists(f):
        raise SystemExit("缺少文件: %s" % f)


def sync_version_files():
    """从根目录 ezcode_version.py 同步两个 PyInstaller 版本资源文件。
    以前版本号散落在 4 处, 改一处忘三处; 现在发版只改 APP_VERSION 一行。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ezcode_version", os.path.join(ROOT, "ezcode_version.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    nums = [int(x) if x.isdigit() else 0
            for x in re.split(r"[.\-]", mod.APP_VERSION)[:4]]
    while len(nums) < 4:
        nums.append(0)
    tup = ", ".join(str(x) for x in nums)
    dotted = ".".join(str(x) for x in nums)
    for path in (VER, os.path.join(HERE, "version_setup.txt")):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            s = f.read()
        s = re.sub(r"filevers=\([^)]*\)", "filevers=(%s)" % tup, s)
        s = re.sub(r"prodvers=\([^)]*\)", "prodvers=(%s)" % tup, s)
        s = re.sub(r"(StringStruct\('FileVersion', ')[^']*(')",
                   lambda m: m.group(1) + dotted + m.group(2), s)
        s = re.sub(r"(StringStruct\('ProductVersion', ')[^']*(')",
                   lambda m: m.group(1) + dotted + m.group(2), s)
        with open(path, "w", encoding="utf-8") as f:
            f.write(s)
    print("[版本] 统一为 %s (%s)" % (mod.APP_VERSION, dotted))


sync_version_files()


# ---------- 公开发布安全门 ----------
# 本机的 config.json / devices.json / knowledge/criteria.json 含真实凭据与内部案例,
# 绝不进入分发包。这里从「公开模板」生成一份干净的首次运行配置。
PUB = os.path.join(HERE, "_public")


def prepare_public():
    default = os.path.join(ROOT, "devices.default.json")
    example = os.path.join(ROOT, "devices.example.json")
    kb_example = os.path.join(ROOT, "knowledge", "criteria.example.json")
    for src in (default, example, kb_example):
        if not os.path.exists(src):
            raise SystemExit("缺少公开模板: %s" % src)
    kb_dir = os.path.join(PUB, "knowledge")
    os.makedirs(kb_dir, exist_ok=True)
    shutil.copyfile(default, os.path.join(PUB, "devices.json"))
    shutil.copyfile(default, os.path.join(PUB, "devices.default.json"))
    shutil.copyfile(example, os.path.join(PUB, "devices.example.json"))
    shutil.copyfile(kb_example, os.path.join(kb_dir, "criteria.json"))
    shutil.copyfile(kb_example, os.path.join(kb_dir, "criteria.example.json"))

    # 硬断言: 任何凭据/本机路径/内网地址混进来就直接终止构建
    blob = b""
    for dp, _, fs in os.walk(PUB):
        for f in fs:
            with open(os.path.join(dp, f), "rb") as fh:
                blob += fh.read()
    forbidden = [("zety" + "un").encode(), ("alaya" + "new").encode(),
                 b"192.168.", b"172.16.",
                 ("马" + "鞍山").encode(), ("青" + "藤").encode(),
                 ("张" + "恩泽").encode()]
    for bad in forbidden:
        if bad in blob:
            raise SystemExit("发布包混入敏感内容, 已中止: %r" % bad)
    for pat in (rb"sk-[A-Za-z0-9]{20,}", rb"nbt_[A-Za-z0-9._-]{20,}",
                rb"C:\\+Users\\+", rb"D:\\+",
                rb"(?<![\d.])10\.\d{1,3}\.\d{1,3}\.\d{1,3}"):
        if re.search(pat, blob):
            raise SystemExit("发布包混入凭据/本机路径/内网地址, 已中止: %r" % pat)
    print("[发布门] 干净首次运行配置已生成: %s" % os.path.relpath(PUB, ROOT))


prepare_public()
if "--check-only" in sys.argv:
    print("发布门检查通过 (未执行打包)")
    sys.exit(0)

# (源, _MEIPASS 内目标) —— 保持与源码目录一致的相对结构
DATAS = [
    (PUB, "."),                                # devices.json + knowledge/criteria.json(公开模板)
    (os.path.join(APP, "webui"), "app/webui"),  # 含松鼠 logo EZcode.png
    (os.path.join(ROOT, "prompts"), "prompts"),
    (os.path.join(ROOT, "skills"), "skills"),      # 内置技能(研判流程等), 随包分发、用户可改
    (os.path.join(ROOT, "agents"), "agents"),
    (os.path.join(ROOT, "plugins"), "plugins"),
    (os.path.join(ROOT, "tools"), "tools"),
    (os.path.join(ROOT, "verify.py"), "."),
    (os.path.join(ROOT, "caps.py"), "."),
    (os.path.join(ROOT, "skills_lib.py"), "."),
    (os.path.join(ROOT, "plugin.py"), "."),
    (os.path.join(ROOT, "plugin_loader.py"), "."),
    (os.path.join(ROOT, "mcp_client.py"), "."),
    (ICO, "."),
]

HIDDEN = [
    "clr", "pythonnet",
    "webview.platforms.edgechromium", "webview.platforms.winforms",
    "win32com.client", "win32com.client.dynamic", "pythoncom", "pywintypes",
    "win32security", "win32process", "win32event", "win32job", "win32con",
    "win32api",
    "verify", "plugin", "plugin_loader", "mcp_client", "caps", "skills_lib",
    "ezcode_common", "ezcode_uninstall", "ezcode_version", "updater", "providers",
    "notify",
    "tools", "tools.ngsoc", "tools.netbox", "tools.edr", "tools.ti",
    "tools.kb", "tools.fs", "tools.web", "tools.permissions",
    "tkinter",
]

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm",
    "--onedir", "--windowed",
    "--name", "EZcode",
    "--distpath", DIST, "--workpath", WORK, "--specpath", SPEC,
    "--icon", ICO, "--version-file", VER,
    "--paths", ROOT, "--paths", APP, "--paths", HERE,
    "--additional-hooks-dir", PYNET_HOOKS,
    "--collect-all", "webview",
    "--collect-all", "pythonnet",
    "--collect-submodules", "tools",
]
for h in HIDDEN:
    cmd += ["--hidden-import", h]
for src, dst in DATAS:
    if os.path.exists(src):
        cmd += ["--add-data", "%s;%s" % (src, dst)]
    else:
        print("[warn] 跳过不存在的资源:", src)
cmd.append(os.path.join(APP, "bootstrap.py"))

print("构建命令:\n ", " ".join(cmd[:18]), "...")
print("\n开始打包 (约 1-3 分钟)...\n")
r = subprocess.run(cmd, cwd=ROOT)
if r.returncode != 0:
    raise SystemExit("PyInstaller 打包失败, code=%s" % r.returncode)

out = os.path.join(DIST, "EZcode", "EZcode.exe")
print("\n构建完成:", out)
print("大小: %.1f MB" % (os.path.getsize(out) / 1024 / 1024))
total = sum(os.path.getsize(os.path.join(dp, f))
            for dp, _, fs in os.walk(os.path.join(DIST, "EZcode")) for f in fs)
print("整个程序目录: %.1f MB" % (total / 1024 / 1024))
