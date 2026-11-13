# -*- coding: utf-8 -*-
"""内置分析脚本工具 run_python: 直接执行Python代码做研判/数据分析。
动机: 模型研判时惯用"写临时脚本文件→shell执行"两步两张工具卡, 干扰对话流;
合并为一步, 且分析代码在权限弹窗中完整可见(逐次确认)。
沙箱: 默认以Windows低完整性令牌在隔离目录执行——脚本写不进沙箱目录之外
(Chrome老沙箱同款完整性隔离), Job Object限内存512MB+超时强杀+关句柄即终止。
配置节 builtin_tools: {"run_python": true, "run_python_sandbox": true}——
设置中心「内置集成」两个独立开关。沙箱初始化失败不静默降级, 明确报错提示可关开关。"""
import shutil
import subprocess

# Windows子进程不弹黑框(CREATE_NO_WINDOW=0x08000000):
# 不加则icacls(沙箱初始化)与普通模式python执行都会闪cmd窗口。
_NO_WINDOW = 0x08000000 if __import__("sys").platform == "win32" else 0

from . import permissions as _perm

_TOOL_NAME = "run_python"
_SANDBOX_MEM_MB = 512
_SCHEMA = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": "[仅可执行模式] 在本机执行一段Python代码做分析(研判思路/数据处理/统计/解析), "
                       "返回stdout/stderr。默认在沙箱中运行(低完整性隔离: 只能写临时沙箱目录, "
                       "512MB内存上限, 代码弹窗给用户确认)。"
                       "适合: 批量解析告警导出/时间序列统计/正则提取/格式转换。超时默认60s(上限300s)。",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "Python代码全文(print输出结果)"},
            "timeout": {"type": "number", "description": "超时秒数, 默认60, 上限300"},
        }, "required": ["code"]},
    },
}
_state = {"registered": False, "sandbox": True}


def _find_python():
    """优先PATH里的python, 其次py -3(Windows启动器)。打包环境无法内嵌解释器, 没有则明确报错。"""
    for cand in ("python", "python3"):
        p = shutil.which(cand)
        if p:
            return p
    if shutil.which("py"):
        return None   # 走py -3的特例
    return None


def configure(cfg):
    """配置驱动注册: builtin_tools.run_python=false 时注销; run_python_sandbox 控制沙箱模式。"""
    from . import register_tool, unregister_tool
    import caps as _caps
    bt = cfg.get("builtin_tools") if isinstance(cfg.get("builtin_tools"), dict) else {}
    _state["sandbox"] = bool(bt.get("run_python_sandbox", True))
    if bt.get("run_python", True):
        # 能力标签从 caps 静态表回填(register 会先摘标签, 重开时必须补回)
        register_tool(_SCHEMA, run_python,
                      capabilities=_caps.STATIC_CAPABILITIES.get(_TOOL_NAME))
        _state["registered"] = True
    else:
        unregister_tool(_TOOL_NAME)
        _state["registered"] = False


_RUNNER = (
    "import sys, traceback\n"
    "_o = open('out.txt', 'w', encoding='utf-8', errors='replace')\n"
    "_e = open('err.txt', 'w', encoding='utf-8', errors='replace')\n"
    "class _W:\n"
    "    def __init__(s, f): s.f = f\n"
    "    def write(s, x): s.f.write(x)\n"
    "    def flush(s): s.f.flush()\n"
    "sys.stdout, sys.stderr = _W(_o), _W(_e)\n"
    "try:\n"
    "    exec(compile(open('user_code.py', encoding='utf-8').read(),"
    " 'user_code.py', 'exec'), {'__name__': '__main__'})\n"
    "except BaseException:\n"
    "    traceback.print_exc()\n"
)


def _sandbox_run(code, t, py_exe):
    """Windows低完整性沙箱执行: 写不出沙箱目录 + 512MB内存 + 超时强杀。"""
    import os
    import tempfile
    import win32security
    import win32process
    import win32con
    import win32api
    import win32event
    import win32job

    sbx = tempfile.mkdtemp(prefix="ezcode-sbx-")
    ph = th = job = None
    try:
        r = subprocess.run(["icacls", sbx, "/setintegritylevel", "(OI)(CI)low"],
                           capture_output=True, timeout=30,
                           creationflags=_NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError("沙箱目录完整性标记失败(rc=%d)" % r.returncode)
        with open(os.path.join(sbx, "user_code.py"), "w", encoding="utf-8") as f:
            f.write(code)
        with open(os.path.join(sbx, "runner.py"), "w", encoding="utf-8") as f:
            f.write(_RUNNER)

        # Job Object: 内存上限 + 主进程退出即终止(防僵尸)
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] = (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY)
        info["ProcessMemoryLimit"] = _SANDBOX_MEM_MB * 1024 * 1024
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info)

        # 复制主进程令牌并降完整性为LOW(写入面被系统强制限制在LOW对象上)
        # 注意: pywin32的DuplicateTokenEx参数顺序与Win32原生不同:
        # (Token, ImpLevel, DesiredAccess, TokenType)
        tok = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_ALL_ACCESS)
        dup = win32security.DuplicateTokenEx(
            tok, 2, win32security.TOKEN_ALL_ACCESS, win32security.TokenPrimary)
        low = win32security.ConvertStringSidToSid("S-1-16-4096")
        win32security.SetTokenInformation(dup, 25, (low, 0))

        si = win32process.STARTUPINFO()
        si.dwFlags |= win32con.STARTF_USESHOWWINDOW
        si.wShowWindow = win32con.SW_HIDE
        cmdline = '"%s" -X utf8 "runner.py"' % py_exe
        ph, th, pid, tid = win32process.CreateProcessAsUser(
            dup, None, cmdline, None, None, 0,
            win32con.NORMAL_PRIORITY_CLASS, None, sbx, si)
        win32job.AssignProcessToJobObject(job, ph)

        timed_out = win32event.WaitForSingleObject(ph, t * 1000) != 0
        if timed_out:
            try:
                win32job.TerminateJobObject(job, 1)
            except Exception:
                pass

        def _read(name):
            p = os.path.join(sbx, name)
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    return f.read()
            except OSError:
                return ""
        out, err = _read("out.txt"), _read("err.txt")
        result = {"sandbox": True, "stdout": out[:12000] or "(无输出——记得print结果)"}
        if err.strip():
            result["stderr"] = err[:3000]
        if timed_out:
            result["error"] = "沙箱内执行超时(%ds), 已强杀(检查死循环/网络等待)" % t
        return result
    except ImportError:
        return {"error": "沙箱初始化失败: 缺少pywin32组件; "
                         "可在设置→内置集成关闭「沙箱执行」后重试"}
    except Exception as e:
        return {"error": "沙箱初始化失败: %s: %s; "
                         "可在设置→内置集成关闭「沙箱执行」后重试"
                         % (type(e).__name__, str(e)[:200])}
    finally:
        import shutil as _sh
        for h in (ph, th, job):
            if h is not None:
                try:
                    win32api.CloseHandle(h)
                except Exception:
                    pass
        _sh.rmtree(sbx, ignore_errors=True)


def run_python(code, timeout=60):
    code = str(code or "")
    if not code.strip():
        return {"error": "code为空"}
    try:
        t = min(max(int(timeout), 5), 300)
    except (TypeError, ValueError):
        t = 60
    py = _find_python()
    argv = None
    if py is None and shutil.which("py"):
        py, argv = "py", ["py", "-3", "-c"]
    if py is None and argv is None:
        return {"error": "本机未找到Python解释器(PATH中的python/py), 无法执行分析脚本"}
    reason, ok = _perm.gate("shell", "run_python(%d字代码)" % len(code),
                            "执行Python分析代码(超时%ds)%s:\n%s"
                            % (t, " · 沙箱模式" if _state["sandbox"] else "", code[:3000]))
    if not ok:
        return {"refused": True, "reason": reason}
    # 沙箱模式: 低完整性令牌 + 隔离目录 + JobObject资源钳制(写不出沙箱目录)
    if _state["sandbox"] and py and argv is None:
        return _sandbox_run(code, t, py)
    # 普通模式(沙箱关闭或py -3启动器): subprocess直跑(权限弹窗仍是第一道防线)
    cmdline = (argv + [code]) if argv else [py, "-c", code]
    try:
        r = subprocess.run(cmdline, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=t,
                           creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"error": "执行超时(%ds): 检查死循环/网络等待, 或缩小任务拆步执行" % t}
    except Exception as e:
        return {"error": "执行失败: %s: %s" % (type(e).__name__, str(e)[:200])}
    out = {"exit_code": r.returncode, "stdout": (r.stdout or "")[:12000]}
    err = (r.stderr or "").strip()
    if err:
        out["stderr"] = err[:3000]
    if not (r.stdout or "").strip():
        out["stdout"] = "(无输出——记得print结果)"
    return out
