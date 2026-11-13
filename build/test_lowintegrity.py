# -*- coding: utf-8 -*-
"""低完整性沙箱端到端试验: 验证(1)子进程写用户目录被拒 (2)写LOW沙箱目录成功。"""
import os
import sys
import json
import shutil
import tempfile
import subprocess

import win32security
import win32process
import win32con
import win32api

# 1. 沙箱目录 + LOW完整性标记
sbx = tempfile.mkdtemp(prefix="ezcode-sbx-test-")
r = subprocess.run(["icacls", sbx, "/setintegritylevel", "(OI)(CI)low"],
                   capture_output=True, timeout=30)
print("icacls标LOW returncode:", r.returncode)

# 2. 复制主令牌并降完整性
# 注意: pywin32的DuplicateTokenEx参数顺序与Win32原生不同: (Token, ImpLevel, DesiredAccess, TokenType)
h = win32api.GetCurrentProcess()
tok = win32security.OpenProcessToken(h, win32security.TOKEN_ALL_ACCESS)
dup = win32security.DuplicateTokenEx(tok, 2, win32security.TOKEN_ALL_ACCESS,
                                     win32security.TokenPrimary)
low = win32security.ConvertStringSidToSid("S-1-16-4096")
win32security.SetTokenInformation(dup, 25, (low, 0))
print("令牌降LOW: OK")

# 3. 低完整性子进程行为验证
code = (
    "import os\n"
    "out = []\n"
    "try:\n"
    "    p = os.path.expanduser('~/ezcode_sbx_test.txt')\n"
    "    open(p, 'w').write('x')\n"
    "    out.append('USER_DIR_WRITE_OK_BAD')\n"
    "except Exception as e:\n"
    "    out.append('USER_DIR_BLOCKED:' + type(e).__name__)\n"
    "try:\n"
    "    open(os.path.join(os.environ['SBX'], 'sandbox_ok.txt'), 'w').write('ok')\n"
    "    out.append('SBX_WRITE_OK')\n"
    "except Exception as e:\n"
    "    out.append('SBX_WRITE_FAIL:' + type(e).__name__)\n"
    "open(os.path.join(os.environ['SBX'], 'result.txt'), 'w').write('|'.join(out))\n"
)
script = os.path.join(sbx, "script.py")
with open(script, "w", encoding="utf-8") as f:
    f.write(code)                     # 写在icacls之后, 文件继承LOW完整性, 低完整性进程可读
cmdline = '"%s" -X utf8 "%s"' % (sys.executable, script)
env = os.environ.copy()
env["SBX"] = sbx
si = win32process.STARTUPINFO()
si.dwFlags |= win32con.STARTF_USESHOWWINDOW
si.wShowWindow = win32con.SW_HIDE
ph, th, pid, tid = win32process.CreateProcessAsUser(
    dup, None, cmdline, None, None, 0,
    win32con.NORMAL_PRIORITY_CLASS, env, sbx, si)
import win32event
rc = win32event.WaitForSingleObject(ph, 30000)
if rc != 0:
    win32process.TerminateProcess(ph, 1)
    print("警告: 进程未在30s内退出, 已强杀")
win32api.CloseHandle(ph)
win32api.CloseHandle(th)
res_path = os.path.join(sbx, "result.txt")
res = open(res_path, encoding="utf-8").read() if os.path.exists(res_path) else "(no result)"
print("沙箱内行为:", res)
print()
print("=== 判定 ===")
print("用户目录写入被阻止:", "USER_DIR_BLOCKED" in res)
print("沙箱目录可正常工作:", "SBX_WRITE_OK" in res)
leftover = os.path.expanduser("~/ezcode_sbx_test.txt")
print("用户目录无残留文件:", not os.path.exists(leftover))
shutil.rmtree(sbx, ignore_errors=True)
