# -*- coding: utf-8 -*-
"""EZcode 安装程序 (tkinter 向导, 单文件 exe)

用法:
  双击            → 图形化安装向导
  --silent        → 静默安装(自动化验收/批量部署), 可配 --dir=... --no-desktop
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile

import ezcode_common as C

APP_TITLE = "EZcode"
BRAND = "#2C53E8"
BRAND_DARK = "#1E3CC0"
LAST_WARNINGS = []
LAST_PRESERVED = []          # 升级安装时保留下来的用户数据文件(相对路径)

# 本机用户数据: 覆盖安装(升级/重装)时不覆盖, 首次安装则正常释放。
# 发布包里这几项只含无凭据默认值; 用户填过的 API Key、接入系统、判据库必须保留。
PRESERVE_USER_FILES = {
    "_internal/devices.json",
    "_internal/config.json",
    "_internal/knowledge/criteria.json",
}


def res(name):
    """PyInstaller onefile 解压目录"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


PAYLOAD = "ezcode-payload.zip"
ICON_PNG = "EZcode-icon.png"


def install(target, desktop, launch, progress=None):
    """执行安装。progress(done, total, text) 可选。返回 (ok, msg)"""
    target = os.path.abspath(target)
    preserved = []
    try:
        if progress:
            progress(0, 100, "准备安装目录…")
        os.makedirs(target, exist_ok=True)

        zpath = res(PAYLOAD)
        if not os.path.exists(zpath):
            return False, "安装数据缺失: %s" % zpath
        with zipfile.ZipFile(zpath) as z:
            entries = z.infolist()
            total = len(entries) or 1
            for i, info in enumerate(entries):
                # 防 Zip Slip
                dest = os.path.normpath(os.path.join(target, info.filename))
                if not dest.startswith(target):
                    continue
                rel = info.filename.replace("\\", "/").lstrip("/")
                if rel in PRESERVE_USER_FILES and os.path.exists(dest):
                    # 升级/重装: 用户填过的接入配置与判据库不能被发布包的空配置覆盖
                    preserved.append(rel)
                    continue
                if info.is_dir():
                    os.makedirs(dest, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    # 自动升级时旧版 EZcode 刚退出, exe 可能仍被占用短暂时间:
                    # 对写占用做几次退避重试, 避免整包安装失败。
                    for attempt in range(4):
                        try:
                            with z.open(info) as src, open(dest, "wb") as out:
                                shutil.copyfileobj(src, out, 1024 * 256)
                            break
                        except PermissionError:
                            if attempt == 3:
                                raise
                            time.sleep(0.8)
                if progress and (i % 8 == 0 or i == total - 1):
                    progress(int(i * 88 / total), 100, "正在释放文件 %d/%d …" % (i + 1, total))

        exe = os.path.join(target, C.EXE_NAME)
        if not os.path.exists(exe):
            return False, "安装后未找到主程序 %s" % exe

        # 顶层放一份图标, 便于资源管理器/快捷方式引用
        src_ico = os.path.join(target, "_internal", "EZcode.ico")
        if os.path.exists(src_ico):
            try:
                shutil.copyfile(src_ico, os.path.join(target, "EZcode.ico"))
            except Exception:
                pass

        if progress:
            progress(90, 100, "创建快捷方式与卸载信息…")
        # 快捷方式/注册表失败不影响程序本体安装成功, 单独记录警告
        global LAST_WARNINGS
        LAST_WARNINGS = []
        try:
            pdir = os.path.join(C.programs_dir(), C.FOLDER_NAME)
            C.shortcut(os.path.join(pdir, "EZcode.lnk"), exe, exe,
                       desc="启动 EZcode")
            C.shortcut(os.path.join(pdir, "卸载 EZcode.lnk"), exe, exe,
                       args="--uninstall", desc="卸载 EZcode")
            if desktop and C.desktop_dir():
                C.shortcut(os.path.join(C.desktop_dir(), "EZcode.lnk"), exe, exe,
                           desc="EZcode")
        except Exception as e:
            LAST_WARNINGS.append("快捷方式: %s: %s" % (type(e).__name__, e))
        try:
            uninstall_cmd = '"%s" --uninstall' % exe
            C.write_uninstall_entry(target, uninstall_cmd)
        except Exception as e:
            LAST_WARNINGS.append("卸载项: %s: %s" % (type(e).__name__, e))

        if progress:
            progress(100, 100, "安装完成")
        if launch:
            try:
                subprocess.Popen([exe], cwd=target,
                                 stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 creationflags=0x08000000, close_fds=True)
            except Exception:
                pass
        LAST_PRESERVED[:] = preserved
        return True, target
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


# ---------------- 图形向导 ----------------
class Wizard:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root = tk.Tk()
        self.root.title("安装 " + APP_TITLE)
        self.root.geometry("660x430")
        self.root.resizable(False, False)
        self.root.configure(bg="white")
        try:
            self.root.iconbitmap(res("EZcode.ico"))
        except Exception:
            pass
        self.desktop = tk.BooleanVar(value=True)
        self.launch = tk.BooleanVar(value=True)
        self.dirvar = tk.StringVar(value=C.default_install_dir())
        self.busy = False

        # 左侧品牌区
        left = tk.Frame(self.root, width=208, bg=BRAND)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        try:
            self.logo = tk.PhotoImage(file=res(ICON_PNG)).subsample(5, 5)
            tk.Label(left, image=self.logo, bg=BRAND).pack(pady=(44, 14))
        except Exception:
            pass
        tk.Label(left, text="EZcode", bg=BRAND, fg="white",
                 font=("Microsoft YaHei", 22, "bold")).pack()
        tk.Label(left, text="版本 " + C.VERSION, bg=BRAND, fg="#AFC4FF",
                 font=("Microsoft YaHei", 9)).pack(side="bottom", pady=22)

        # 右侧内容
        right = tk.Frame(self.root, bg="white")
        right.pack(side="right", fill="both", expand=True)
        self.head = tk.Label(right, text="", bg="white", fg="#1B2333",
                            font=("Microsoft YaHei", 15, "bold"), anchor="w")
        self.head.pack(fill="x", padx=28, pady=(30, 12))
        self.body = tk.Frame(right, bg="white")
        self.body.pack(fill="both", expand=True, padx=28)

        bar = tk.Frame(right, bg="#F6F8FC", height=62)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.cancel = tk.Button(bar, text="取消", width=9, bg="#EDF1F8", fg="#4A5568",
                                relief="flat", font=("Microsoft YaHei", 9),
                                command=self.root.destroy)
        self.cancel.pack(side="left", padx=(28, 0), pady=14)
        self.next = tk.Button(bar, text="下一步", width=11, bg=BRAND, fg="white",
                              relief="flat", activebackground=BRAND_DARK,
                              activeforeground="white", font=("Microsoft YaHei", 9, "bold"),
                              command=self.on_next)
        self.next.pack(side="right", padx=(0, 28), pady=14)
        self.page = 0
        self.page_welcome()

    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def label(self, text, fg="#5A6478", size=9, **kw):
        l = self.tk.Label(self.body, text=text, bg="white", fg=fg,
                          font=("Microsoft YaHei", size), justify="left",
                          wraplength=390, anchor="w", **kw)
        l.pack(fill="x", pady=(0, 8))
        return l

    def page_welcome(self):
        self.page = 0
        self.head.configure(text="欢迎安装 EZcode")
        self.clear()
        self.label("这是一款本地运行的多场景桌面 AI 应用：\n\n"
                   "· 安全运营研判、编写项目代码、日常工作助手，随时切换专家\n"
                   "· 原生 Windows 窗口，专属图标，无需浏览器或命令行\n"
                   "· 流式思考、工具证据、权限确认与会话管理；外部系统按需接入\n"
                   "· 全部数据保存在本机，绿色卸载不残留\n\n"
                   "点击「下一步」选择安装位置。", size=10)
        self.next.configure(text="下一步", state="normal")

    def page_options(self):
        self.page = 1
        self.head.configure(text="选择安装位置")
        self.clear()
        self.label("安装到（无需管理员权限）：", fg="#333B4D", size=10)
        row = self.tk.Frame(self.body, bg="white")
        row.pack(fill="x", pady=(0, 12))
        e = self.tk.Entry(row, textvariable=self.dirvar, font=("Microsoft YaHei", 9),
                          relief="solid", bd=1, highlightthickness=0)
        e.pack(side="left", fill="x", expand=True, ipady=5)
        self.tk.Button(row, text="浏览…", width=7, relief="flat", bg="#EDF1F8",
                       font=("Microsoft YaHei", 9),
                       command=self.browse).pack(side="left", padx=(6, 0))
        self.tk.Checkbutton(self.body, text="创建桌面快捷方式", variable=self.desktop,
                            bg="white", font=("Microsoft YaHei", 9),
                            activebackground="white", anchor="w").pack(fill="x", pady=2)
        self.tk.Checkbutton(self.body, text="安装完成后启动 EZcode", variable=self.launch,
                            bg="white", font=("Microsoft YaHei", 9),
                            activebackground="white", anchor="w").pack(fill="x", pady=2)
        self.label("约占用 60 MB 磁盘空间。已安装旧版本会被自动替换。", fg="#939CB0", size=9)
        self.next.configure(text="开始安装", state="normal")

    def browse(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=os.path.dirname(self.dirvar.get()))
        if d:
            self.dirvar.set(os.path.join(d, C.FOLDER_NAME))

    def page_progress(self):
        self.page = 2
        self.head.configure(text="正在安装…")
        self.clear()
        self.label("请稍候，正在释放程序文件。", size=10)
        self.pbar = self.ttk.Progressbar(self.body, length=380, mode="determinate", maximum=100)
        self.pbar.pack(pady=(16, 8))
        self.status = self.label("准备中…", fg="#7A8498", size=9)
        self.next.configure(text="安装中", state="disabled")
        self.busy = True

    def page_done(self, ok, msg):
        self.page = 3
        self.busy = False
        self.cancel.configure(text="关闭", state="normal")
        self.next.configure(text="完成", state="normal", command=self.root.destroy)
        self.head.configure(text="安装完成" if ok else "安装失败")
        self.clear()
        if ok:
            self.label("✔  EZcode 已成功安装", fg="#16804A", size=13)
            self.label("安装位置：%s\n\n开始菜单已创建程序组与卸载入口，"
                       "可在「设置 → 应用 → 已安装的应用」中卸载。" % msg, size=10)
            if LAST_PRESERVED:
                self.label("升级安装已保留原有接入配置、密钥与判据库。",
                           fg="#16804A", size=9)
        else:
            self.label("✘  %s" % msg, fg="#C0392B", size=10)

    def on_next(self):
        if self.page == 0:
            self.page_options()
        elif self.page == 1:
            self.page_progress()
            threading.Thread(target=self._work, daemon=True).start()
        elif self.page == 3:
            self.root.destroy()

    def _work(self):
        def prog(done, total, text):
            def upd():
                try:
                    self.pbar["value"] = done
                    self.status.configure(text=text)
                except Exception:
                    pass
            self.root.after(0, upd)
        ok, msg = install(self.dirvar.get(), self.desktop.get(), self.launch.get(), prog)
        self.root.after(120, lambda: self.page_done(ok, msg))


def main():
    args = sys.argv[1:]
    if "--silent" in args:
        target = C.default_install_dir()
        for a in args:
            if a.startswith("--dir="):
                target = a.split("=", 1)[1]
        desktop = "--no-desktop" not in args
        launch = "--launch" in args          # 自动更新装完后重新启动 EZcode
        ok, msg = install(target, desktop, launch)
        # windowed exe 无 stdout, 结果写文件供自动化验收读取
        import tempfile
        result = os.path.join(tempfile.gettempdir(), "ezcode-install-result.txt")
        for a in args:
            if a.startswith("--result="):
                result = a.split("=", 1)[1]
        try:
            with open(result, "w", encoding="utf-8") as f:
                f.write(("OK " if ok else "FAIL ") + str(msg) +
                        ("\nWARN " + "; ".join(LAST_WARNINGS) if LAST_WARNINGS else "") +
                        ("\nPRESERVED " + "; ".join(LAST_PRESERVED)
                         if LAST_PRESERVED else ""))
        except Exception:
            pass
        if LAST_PRESERVED:
            print("升级安装已保留原有配置: " + ", ".join(LAST_PRESERVED))
        return 0 if ok else 1
    w = Wizard()
    w.root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
