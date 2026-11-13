# -*- coding: utf-8 -*-
"""run_python 沙箱模式全链路冒烟。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, ROOT)

ev = []
eng = None


def emit(p):
    ev.append(p)
    if p.get("t") == "confirm":
        eng.confirm_result(p["seq"], "once")


eng = __import__("engine").Engine(emit=emit)
from tools import HANDLERS  # noqa: E402

eng.set_mode("execute")

# 1. 沙箱模式执行(默认开)
r = HANDLERS["run_python"](**{"code": "print(40+2)", "timeout": 60})
print("沙箱执行42:", r.get("sandbox") is True and r.get("stdout", "").strip() == "42",
      "| stdout:", r.get("stdout", "").strip())
print("无报错:", "error" not in r)

# 2. 沙箱内写用户目录 → 应被系统拒绝(真隔离)
r = HANDLERS["run_python"](**{"code": (
    "import os\n"
    "try:\n"
    "    open(os.path.expanduser('~/ezcode_sbx_attack.txt'), 'w').write('pwn')\n"
    "    print('USER_WRITE_OK_BAD')\n"
    "except Exception as e:\n"
    "    print('USER_WRITE_BLOCKED:', type(e).__name__)\n"
)})
print("沙箱阻止写用户目录:", "USER_WRITE_BLOCKED" in r.get("stdout", ""))
print("用户目录无残留:", not os.path.exists(os.path.expanduser("~/ezcode_sbx_attack.txt")))

# 3. 沙箱内可正常写沙箱目录(cwd隔离)
r = HANDLERS["run_python"](**{"code": (
    "import os; open('result.csv','w').write('a,b\\n1,2'); print(os.getcwd())"
)})
print("沙箱内文件可写(cwd隔离):", "ezcode-sbx" in r.get("stdout", ""))

# 4. 异常捕获
r = HANDLERS["run_python"](**{"code": "1/0"})
print("异常stderr可见:", "ZeroDivisionError" in r.get("stderr", ""))

# 5. 超时强杀
t0 = time.time()
r = HANDLERS["run_python"](**{"code": "while True: pass", "timeout": 5})
print("超时强杀(~5s):", "超时" in str(r.get("error", "")) and 4 < time.time() - t0 < 20)

# 6. 关闭沙箱开关 → 普通模式
eng.save_settings({"builtin_tools": {"run_python": True, "run_python_sandbox": False}})
r = HANDLERS["run_python"](**{"code": "print('plain-mode')"})
print("沙箱关闭走普通模式:", r.get("sandbox") is None and r.get("stdout", "").strip() == "plain-mode")

# 7. 重开沙箱
eng.save_settings({"builtin_tools": {"run_python": True, "run_python_sandbox": True}})
r = HANDLERS["run_python"](**{"code": "print('sbx-again')"})
print("沙箱重开生效:", r.get("sandbox") is True and r.get("stdout", "").strip() == "sbx-again")
