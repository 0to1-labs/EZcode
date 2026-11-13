# -*- coding: utf-8 -*-
"""统计冷启动阶段各模块导入耗时 (-X importtime), 找出真正的大头。"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")

code = "import sys;sys.path.insert(0,%r);sys.path.insert(0,%r);import client" % (APP, ROOT)
try:
    p = subprocess.run([sys.executable, "-X", "importtime", "-c", code],
                       capture_output=True, text=True, cwd=ROOT, timeout=90)
except subprocess.TimeoutExpired:
    print("导入超时 (>90s)")
    sys.exit(1)

recs = []
for line in p.stderr.splitlines():
    m = re.match(r"import time:\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(.*)", line)
    if m:
        recs.append((int(m.group(1)), int(m.group(2)), m.group(3).strip()))

print("returncode=%s  导入条目=%d  自耗时合计=%.1fms" % (
    p.returncode, len(recs), sum(r[0] for r in recs) / 1000))
print("\n-- 累计耗时 top 20 --")
for s, c, n in sorted(recs, key=lambda x: -x[1])[:20]:
    print("%8.1fms  %s" % (c / 1000, n))
print("\n-- 自身耗时 top 20 --")
for s, c, n in sorted(recs, key=lambda x: -x[0])[:20]:
    print("%8.1fms  %s" % (s / 1000, n))
if p.stdout.strip():
    print("\nSTDOUT:", p.stdout[:500])
if p.returncode != 0:
    tail = [l for l in p.stderr.splitlines() if not l.startswith("import time:")]
    print("\nERR:", "\n".join(tail[-15:]))
