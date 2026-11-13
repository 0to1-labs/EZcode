# -*- coding: utf-8 -*-
"""把 dist/EZcode 程序目录压成安装器内嵌的 payload"""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "dist-fast", "EZcode")
OUT = os.path.join(HERE, "ezcode-payload.zip")

if not os.path.exists(os.path.join(SRC, "EZcode.exe")):
    raise SystemExit("未找到已打包的程序目录: %s" % SRC)

# 跳过构建/验收阶段产生的会话与日志(不删除任何文件), 安装后是干净首次运行
def skip(rel):
    parts = rel.replace("\\", "/").split("/")
    return "logs" in parts or rel.endswith(".pyc")


n = 0
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for dp, _, fs in os.walk(SRC):
        for f in fs:
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, SRC)
            if skip(rel):
                continue
            z.write(full, rel)
            n += 1
print("payload: %s (%d 个文件, %.1f MB)" % (OUT, n, os.path.getsize(OUT) / 1024 / 1024))
