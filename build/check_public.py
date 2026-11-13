# -*- coding: utf-8 -*-
"""开源发布前自检: 确认「将被 git 提交的文件」和「安装包内的配置」不含敏感信息。

用法:  python build/check_public.py
退出码 0 = 通过; 1 = 发现必须修复的问题。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))

TEXT_EXT = {".py", ".js", ".css", ".html", ".json", ".md", ".bat", ".pyw",
            ".txt", ".spec", ".gitignore", ".yml", ".yaml", ".toml"}
SKIP_DIRS = {"dist", "dist-fast", "dist-final", "installer-dist",
             "installer-dist-fast", "installer-dist-final", "work", "work-fast",
             "work-final", "installer-work", "installer-work-fast",
             "installer-work-final", "spec-fast", "spec-final",
             "spec-setup-fast", "spec-setup-final", "__pycache__", "logs",
             ".git", ".workbuddy"}

# (级别, 说明, 正则) —— ERROR 会阻断发布
# 敏感词用拼接写法, 避免本脚本自身被自己的规则命中
_SENSITIVE = ("zety" + "un|" + "alaya" + "new|" + "马" + "鞍山|" + "张" + "恩泽|"
              + "secops" + "-agent|" + "南沙" + "湾|" + "宁" + "广|"
              + "E" + "联|" + "西" + "湖|" + "黄" + "海|" + "黄" + "山")
RULES = [
    ("ERROR", "真实 API Key", r"sk-[A-Za-z0-9]{20,}"),
    ("ERROR", "资产库 Token", r"nbt_[A-Za-z0-9._-]{20,}"),
    ("ERROR", "本机绝对路径", r"[A-Z]:\\{1,2}Users\\{1,2}"),
    ("ERROR", "内网/私有地址", r"(?<![\d.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.1[6-9]\.\d{1,3}\.\d{1,3})"),
    ("ERROR", "内部域名/人名/项目名", _SENSITIVE),
    ("WARN", "旧品牌残留", r"(?i)seccop"),
    ("WARN", "厂商品牌词", r"奇安信|青藤|微步在线|NGSOC"),
]
PRIVATE = {"config.json", "devices.json", "knowledge/criteria.json"}


def candidates():
    """优先用 git 的 exclude-standard 结果(与 .gitignore 完全一致)"""
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        files = [l.strip().replace("\\", "/") for l in out.splitlines() if l.strip()]
        if files:
            return files, "git ls-files"
    except Exception:
        pass
    files = []
    for dp, dirs, fs in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in fs:
            rel = os.path.relpath(os.path.join(dp, f), ROOT).replace("\\", "/")
            if rel in PRIVATE or rel.startswith("build/_"):
                continue
            # 模拟 .gitignore 中「构建产物」一节
            if rel.startswith("build/") and (
                    f.endswith((".spec", ".zip", ".exe", ".toc", ".pyc"))
                    or "松鼠" in f or "squirrel-source" in f or "preview" in f):
                continue
            if f.endswith(".exe") or f.endswith(".pyc"):
                continue
            if os.path.splitext(f)[1].lower() in TEXT_EXT:
                files.append(rel)
    return files, "目录遍历(未检测到 git 仓库)"


def scan(text, label):
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for lvl, desc, pat in RULES:
            if re.search(pat, line):
                hits.append((lvl, desc, i, line.strip()[:90]))
    return hits


def main():
    files, source = candidates()
    print("EZcode 发布前自检 | 文件来源: %s | 共 %d 个文件\n" % (source, len(files)))

    errors = 0
    warns = 0
    for rel in files:
        if rel.endswith("check_public.py"):     # 规则表本身含敏感词样本, 跳过
            continue
        if os.path.splitext(rel)[1].lower() not in TEXT_EXT and \
           os.path.basename(rel) != ".gitignore":
            continue
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, OSError):
            continue
        for lvl, desc, ln, snippet in scan(text, rel):
            if lvl == "ERROR":
                errors += 1
            else:
                warns += 1
            print("  [%s] %-46s L%-4d %-14s %s" % (lvl, rel, ln, desc, snippet))

    # 私有文件若被 git 跟踪, 直接判失败
    tracked_priv = [f for f in files if f in PRIVATE]
    for f in tracked_priv:
        errors += 1
        print("  [ERROR] 私有文件将被提交: %s" % f)

    # 安装版里的实际配置(打包后才有)
    shipped = os.path.join(HERE, "dist-fast", "EZcode", "_internal", "devices.json")
    if os.path.exists(shipped):
        with open(shipped, encoding="utf-8") as f:
            hits = scan(f.read(), shipped)
        for lvl, desc, ln, snippet in hits:
            if lvl == "ERROR":
                errors += 1
            else:
                warns += 1
            print("  [%s] 安装包 devices.json L%-4d %-14s %s" % (lvl, ln, desc, snippet))
        print("  [INFO] 已检查安装包内配置: %s" % os.path.relpath(shipped, ROOT))

    print("\n结果: ERROR %d / WARN %d" % (errors, warns))
    if errors:
        print("❌ 存在必须修复的敏感信息, 请勿 push / 发布。")
        return 1
    print("✅ 通过: 未发现凭据、本机路径或内部案例。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
