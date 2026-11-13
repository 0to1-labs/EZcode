# -*- coding: utf-8 -*-
"""首屏握手延迟诊断: 同时采集 pywebview DEBUG 日志与启动打点。

用法: python diag_boot.py [等待秒数]
在单进程内闭环(启动→等待→回收), 避免外层 shell 结束时误杀 GUI。
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
CLIENT = os.path.join(ROOT, "app", "client.py")
CMD = json.loads(os.environ["SECOP_CMD"]) if os.environ.get("SECOP_CMD") \
    else [PY, "-X", "utf8", CLIENT]
WAIT = float(sys.argv[1]) if len(sys.argv) > 1 else 28.0

stamp = str(int(time.time() * 1000) % 100000000)
prof = os.path.join(tempfile.gettempdir(), "ezcode-diag-%s.prof" % stamp)
log = os.path.join(tempfile.gettempdir(), "ezcode-diag-%s.log" % stamp)

env = dict(os.environ)
env["PYWEBVIEW_LOG"] = "DEBUG"
env["SECOP_BOOT_PROFILE"] = prof
env.setdefault("SECOP_PRIVATE_MODE", "0")

t0 = time.perf_counter()
with open(log, "w", encoding="utf-8", errors="replace") as f:
    p = subprocess.Popen(CMD, cwd=ROOT, env=env, stdout=f,
                         stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL,
                         creationflags=0x08000000, close_fds=True)
    booted = False
    while time.perf_counter() - t0 < WAIT:
        if os.path.isfile(prof):
            try:
                with open(prof, encoding="utf-8") as pf:
                    if "bootstrap_done" in pf.read():
                        booted = True
                        break
            except Exception:
                pass
        time.sleep(0.1)
    elapsed = time.perf_counter() - t0
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=0x08000000)

print("握手完成=%s  用时=%.2fs" % (booted, elapsed))
print("\n=== 启动打点 (相对 client 加载) ===")
if os.path.isfile(prof):
    with open(prof, encoding="utf-8") as f:
        print(f.read().strip())
print("\n=== pywebview 日志 ===")
if os.path.isfile(log):
    with open(log, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    print("总行数:", len(lines))
    for l in lines[:120]:
        print(l)
    if len(lines) > 120:
        print("... 末尾 25 行 ...")
        for l in lines[-25:]:
            print(l)
print("\n日志文件:", log)
