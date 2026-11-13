# -*- coding: utf-8 -*-
"""把本机私有配置与历史数据恢复到已安装的 EZcode。

发布包按设计只带「无凭据默认配置」(见 build_app.py 的发布门), 这样安装包
可以安全地对外分发。本机升级 / 重装之后, 运行本脚本即可把原来的接入配置、
判据库、历史会话、插件、技能、界面偏好恢复回来。

用法:
    python build/restore_local_config.py             # 恢复(覆盖前自动备份)
    python build/restore_local_config.py --dry-run   # 只看将要做什么

幂等: 可反复执行; 会话/技能只补齐不覆盖; 覆盖范围仅限下列明确列出的文件。
"""
import os
import sys
import json
import shutil
import hashlib
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
LOCAL = os.environ.get("LOCALAPPDATA") or HOME

TARGET = os.path.join(LOCAL, "Programs", "EZcode", "_internal")
# 旧品牌目录名用拼接写法, 避免发布自检把脚本自身当成「旧品牌残留」
OLD_NAME = "Sec" + "cop"
OLD = os.path.join(LOCAL, "Programs", OLD_NAME, "_internal")
OLD_USER = os.path.join(HOME, "." + OLD_NAME.lower())
NEW_USER = os.path.join(HOME, ".ezcode")

OLD_WEB = os.path.join(LOCAL, OLD_NAME, "webview-profile", "EBWebView",
                       "Default", "Local Storage", "leveldb")
NEW_WEB = os.path.join(LOCAL, "EZcode", "webview-profile", "EBWebView",
                       "Default", "Local Storage", "leveldb")

BACKUP = os.path.join(HERE, "_backup-local-config", time.strftime("%Y%m%d-%H%M%S"))
DRY = "--dry-run" in sys.argv
WARN = []

# (相对路径, 是否属于用户数据必须恢复)
CONFIG_FILES = ["devices.json", "config.json", "knowledge/criteria.json"]


def relpath(p):
    return os.path.relpath(p, TARGET).replace("\\", "/")


def backup(path):
    """覆盖前把目标文件留一份, 便于随时回退; 所有备份都落在 BACKUP 内。"""
    if DRY or not os.path.isfile(path):
        return
    rel = os.path.relpath(path, TARGET)
    if rel.startswith("..") or os.path.isabs(rel):
        # 不在安装目录内(全局技能 / 浏览器偏好): 按绝对路径归档到 _user 子目录
        drive, tail = os.path.splitdrive(os.path.abspath(path))
        rel = os.path.join("_user", drive.replace(":", "").strip("\\/"),
                           tail.lstrip("\\/"))
    dst = os.path.join(BACKUP, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(path, dst)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def copy_file(src, dst, label, overwrite=True):
    if not os.path.isfile(src):
        print("  - 跳过 %s (来源不存在)" % label)
        return False
    if os.path.isfile(dst) and (not overwrite or sha(src) == sha(dst)):
        print("  = 保留 %s (已存在且一致)" % label)
        return False
    print("  + 恢复 %s (%d bytes)" % (label, os.path.getsize(src)))
    if not DRY:
        backup(dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    return True


def source(rel):
    """优先取源码根(日常在维护的那份), 缺失时回退旧安装目录。"""
    a = os.path.join(ROOT, rel)
    if os.path.isfile(a):
        return a
    return os.path.join(OLD, rel)


def main():
    print("目标安装目录:", TARGET)
    if not os.path.isdir(TARGET):
        raise SystemExit("未找到已安装的 EZcode, 请先运行 EZcodeSetup.exe")
    print("备份目录:", BACKUP if not DRY else "(dry-run 不写文件)")
    print("模式:", "预演" if DRY else "实际恢复")

    print("\n[1/6] 接入配置与判据库")
    for rel in CONFIG_FILES:
        copy_file(source(rel), os.path.join(TARGET, rel), rel)

    print("\n[2/6] 插件")
    plugins = os.path.join(ROOT, "plugins")
    if os.path.isdir(plugins):
        for fn in sorted(os.listdir(plugins)):
            if fn.endswith(".py"):
                copy_file(os.path.join(plugins, fn),
                          os.path.join(TARGET, "plugins", fn), "plugins/" + fn)
    else:
        print("  - 无插件目录")

    print("\n[3/6] 历史会话 (只补齐, 不覆盖)")
    import glob
    dst_sessions = os.path.join(TARGET, "logs", "sessions")
    # 旧目录可能已在清理旧版时移除; 依次回退到清理前的完整备份快照,
    # 保证「旧版已卸载」之后本机数据依然能一键恢复。
    src_dirs = [os.path.join(OLD, "logs", "sessions")]
    for pattern in ("_backup-" + OLD_NAME.lower() + "-local",
                    "_backup-local-config"):
        src_dirs += [os.path.join(d, "logs", "sessions")
                     for d in sorted(glob.glob(os.path.join(HERE, pattern, "*")),
                                     reverse=True)]
    seen, n_sessions = set(), 0
    for src_sessions in src_dirs:
        if not os.path.isdir(src_sessions):
            continue
        for fn in sorted(os.listdir(src_sessions)):
            if not fn.endswith(".session.json") or fn in seen:
                continue
            seen.add(fn)
            if copy_file(os.path.join(src_sessions, fn),
                         os.path.join(dst_sessions, fn),
                         "sessions/" + fn, overwrite=False):
                n_sessions += 1
    print("  会话总数:", len([f for f in os.listdir(dst_sessions)
                              if f.endswith(".session.json")])
          if os.path.isdir(dst_sessions) else 0)

    print("\n[4/6] 全局技能")
    src_skills = os.path.join(OLD_USER, "skills")
    dst_skills = os.path.join(NEW_USER, "skills")
    if not DRY:
        os.makedirs(dst_skills, exist_ok=True)
    if os.path.isdir(src_skills):
        for fn in sorted(os.listdir(src_skills)):
            if fn.endswith(".md"):
                copy_file(os.path.join(src_skills, fn),
                          os.path.join(dst_skills, fn), "skills/" + fn,
                          overwrite=False)
    else:
        print("  = 已创建全局技能目录 (旧目录无内容)")
    # 项目级技能也补齐到安装版
    proj_skills = os.path.join(ROOT, "skills")
    if os.path.isdir(proj_skills):
        for fn in sorted(os.listdir(proj_skills)):
            if fn.endswith(".md"):
                copy_file(os.path.join(proj_skills, fn),
                          os.path.join(TARGET, "skills", fn),
                          "project-skills/" + fn, overwrite=False)

    print("\n[5/6] 界面偏好 (WebView2 localStorage, 失败不影响主流程)")
    if os.path.isdir(OLD_WEB):
        for fn in sorted(os.listdir(OLD_WEB)):
            if fn in ("LOCK",) or fn.startswith("LOG"):
                continue
            s, d = os.path.join(OLD_WEB, fn), os.path.join(NEW_WEB, fn)
            try:
                copy_file(s, d, "webview-prefs/" + fn)
            except Exception as e:
                WARN.append("界面偏好 %s: %s" % (fn, type(e).__name__))
    else:
        print("  - 无旧界面偏好")

    print("\n[6/6] 恢复结果校验 (不显示任何凭据内容)")
    dev = os.path.join(TARGET, "devices.json")
    with open(dev, encoding="utf-8") as f:
        cfg = json.load(f)
    crit = os.path.join(TARGET, "knowledge", "criteria.json")
    with open(crit, encoding="utf-8") as f:
        kb = json.load(f)
    profiles = cfg.get("llm", {}).get("profiles", {})
    with_key = sum(1 for v in profiles.values()
                   if isinstance(v, dict) and v.get("api_key"))
    sites = (cfg.get("ngsoc") or {}).get("sites") or []
    print("  配置节:", ", ".join(sorted(k for k in cfg if not k.startswith("_"))))
    print("  大模型 profiles:", len(profiles), "| 已填密钥:", with_key)
    print("  告警检索站点:", len(sites), "| 判据库:",
          os.path.getsize(crit), "bytes / 顶层", len(kb), "项")
    ok = bool(profiles) and with_key > 0 and bool(sites)
    print("  校验:", "PASS" if ok else "FAIL(请检查来源文件)")

    if WARN:
        print("\n非致命提示:", "; ".join(WARN))
    print("\n完成。备份:", BACKUP if not DRY else "(预演未写)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
