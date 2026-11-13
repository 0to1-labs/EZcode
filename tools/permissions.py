# -*- coding: utf-8 -*-
"""EZcode 权限引擎 (对齐OpenCode V2 permissions: allow/ask/deny, 最后匹配规则生效)
action: shell / file_read / file_write / dispose / kb_write / mcp
resource: 命令文本/路径, fnmatch通配符
规则来源: 内置默认规则 + devices.json的permissions(追加, 后匹配覆盖先匹配)
默认策略: 破坏性命令/敏感文件读取=deny; 其余shell/写文件=ask(用户逐次确认); 普通文件读=allow
"""
import fnmatch
import subprocess

# Windows子进程不弹黑框: CREATE_NO_WINDOW=0x08000000。
# 不加这个标志, shell工具每次执行都会闪一个cmd窗口(用户允许执行后看到的"闪一下")。
_NO_WINDOW = 0x08000000 if __import__("sys").platform == "win32" else 0

DEFAULT_RULES = [
    # ---- 宽泛基线规则(在前, 特例在后覆盖; 最后匹配生效) ----
    {"action": "file_read", "resource": "*", "effect": "allow"},
    {"action": "shell", "resource": "*", "effect": "ask"},
    {"action": "file_write", "resource": "*", "effect": "ask"},
    {"action": "dispose", "resource": "*", "effect": "ask"},
    # ---- 破坏性命令: 一律拒绝 ----
    # 注: 黑名单只防常见误触与低级绕过, 不可能完备(PowerShell别名/编码执行等绕过面很大);
    #     注入威胁下的真正防线是ask弹窗里的用户确认——弹窗会展示完整命令文本。
    {"action": "shell", "resource": "rm *", "effect": "deny"},
    {"action": "shell", "resource": "del *", "effect": "deny"},
    {"action": "shell", "resource": "rd *", "effect": "deny"},
    {"action": "shell", "resource": "ri *", "effect": "deny"},           # Remove-Item别名
    {"action": "shell", "resource": "erase *", "effect": "deny"},       # Remove-Item别名
    {"action": "shell", "resource": "clear *", "effect": "deny"},       # Clear-Content等
    {"action": "shell", "resource": "Remove-Item*", "effect": "deny"},
    {"action": "shell", "resource": "*;Remove-Item*", "effect": "deny"},
    {"action": "shell", "resource": "*|Remove-Item*", "effect": "deny"},
    {"action": "shell", "resource": "*;ri *", "effect": "deny"},
    {"action": "shell", "resource": "*|ri *", "effect": "deny"},
    {"action": "shell", "resource": "cmd /c *del*", "effect": "deny"},  # cmd包装绕过
    {"action": "shell", "resource": "cmd /c *rm*", "effect": "deny"},
    {"action": "shell", "resource": "format *", "effect": "deny"},
    {"action": "shell", "resource": "shutdown*", "effect": "deny"},
    {"action": "shell", "resource": "reg *", "effect": "deny"},
    {"action": "shell", "resource": "regedit*", "effect": "deny"},
    {"action": "shell", "resource": "cipher /w*", "effect": "deny"},
    {"action": "shell", "resource": "iex *", "effect": "deny"},         # Invoke-Expression下载执行
    {"action": "shell", "resource": "*;iex*", "effect": "deny"},
    {"action": "shell", "resource": "*|iex*", "effect": "deny"},
    # ---- 防火墙/网络阻断类变更: 一律拒绝 ----
    # 设计红线: 封禁IP/加白只能给建议, 由用户在防火墙平台手动执行。
    # shell 是唯一可能"绕道真去封禁"的通道, 这里在权限层硬拦(ask也不放行),
    # 避免用户误点"本会话总是允许"后由agent静默改动网络策略。
    {"action": "shell", "resource": "*New-NetFirewallRule*", "effect": "deny"},
    {"action": "shell", "resource": "*Set-NetFirewallRule*", "effect": "deny"},
    {"action": "shell", "resource": "*Enable-NetFirewallRule*", "effect": "deny"},
    {"action": "shell", "resource": "*New-NetIPSecRule*", "effect": "deny"},
    {"action": "shell", "resource": "*netsh*advfirewall*", "effect": "deny"},
    {"action": "shell", "resource": "*netsh*ipsec*", "effect": "deny"},
    {"action": "shell", "resource": "*iptables*", "effect": "deny"},
    {"action": "shell", "resource": "*nft*add*rule*", "effect": "deny"},
    {"action": "shell", "resource": "*route*add*", "effect": "deny"},   # 黑洞路由封禁
    {"action": "shell", "resource": "*Set-NetIPAddress*", "effect": "deny"},
    # ---- 敏感文件读取: 一律拒绝 ----
    {"action": "file_read", "resource": "*.ssh*", "effect": "deny"},
    {"action": "file_read", "resource": "*id_rsa*", "effect": "deny"},
    {"action": "file_read", "resource": "*id_ed25519*", "effect": "deny"},
    {"action": "file_read", "resource": "*credential*", "effect": "deny"},
    {"action": "file_read", "resource": "*secret*", "effect": "deny"},
    {"action": "file_read", "resource": "*token*", "effect": "deny"},
    {"action": "file_read", "resource": "*.kdbx", "effect": "deny"},
    {"action": "file_read", "resource": "*密码*", "effect": "deny"},
]

_rules = list(DEFAULT_RULES)
_session_grants = {}      # scope(会话sid) -> [(action, pattern)]; "本会话总是允许"按任务隔离
_scope = None             # 由 Engine 注入: () -> 当前线程所属会话 sid
_confirm = None           # 交互确认回调(由UI注入): (action, resource, detail) -> bool


def load_rules(cfg):
    """加载默认规则+devices.json自定义规则(追加在后, 后匹配覆盖)"""
    global _rules
    _rules = list(DEFAULT_RULES) + [r for r in (cfg.get("permissions") or []) if r]
    _session_grants.clear()


def set_scope(fn):
    """注入"当前会话"解析回调; 多任务下同一进程的不同任务授权互不串台。"""
    global _scope
    _scope = fn


def _cur_scope():
    try:
        return _scope() if _scope else None
    except Exception:
        return None


def set_confirm(fn):
    """UI注入确认回调(TUI弹窗/CLI输入)"""
    global _confirm
    _confirm = fn



def _session_ok(action, resource, scope):
    for a, p in _session_grants.get(scope, ()):
        if (a == "*" or fnmatch.fnmatch(action, a)) and fnmatch.fnmatch(str(resource), p):
            return True
    return False


def check(action, resource, scope=None):
    """权限判定: 会话授权 > 规则(最后匹配生效) > ask。scope 缺省时按当前会话解析。"""
    if _session_ok(action, resource, _cur_scope() if scope is None else scope):
        return "allow"
    verdict = "ask"
    for rule in _rules:
        a = rule.get("action", "*")
        if a != "*" and not fnmatch.fnmatch(action, a):
            continue
        if fnmatch.fnmatch(str(resource), rule.get("resource", "*")):
            verdict = rule.get("effect", "ask")
    return verdict


def grant_session(action, pattern, scope=None):
    """用户选'本会话总是允许'时登记授权; 只对当前任务生效, 切到别的任务不继承。"""
    key = _cur_scope() if scope is None else scope
    _session_grants.setdefault(key, []).append((action, pattern))


def _gate(action, resource, detail):
    """ask路径走交互确认; 返回(None, True)=通过, (原因, False)=拒绝"""
    verdict = check(action, resource)
    if verdict == "allow":
        return None, True
    if verdict == "deny":
        return ("权限规则拒绝(deny): %s。如确需执行, 请用户手动操作或在devices.json的permissions中调整规则"
                % resource), False
    if _confirm is None:
        return ("无可用的交互确认通道(非交互模式), 拒绝执行: %s" % resource), False
    if not _confirm(action, resource, detail):
        return ("用户拒绝执行: %s" % resource), False
    return None, True


def gate(action, resource, detail=""):
    """公开权限门: 写操作工具(告警平台处置回写/知识库写入等)统一走这里。
    LLM自报的confirmed参数不可作为执行依据——人工确认弹窗是唯一防线。"""
    return _gate(action, resource, detail)


def run_shell_cmd(cmd, timeout=60):
    """PowerShell执行命令(权限无关的底层执行)——CREATE_NO_WINDOW防闪黑框"""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           creationflags=_NO_WINDOW)
        return (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return "", "执行失败: %s" % e


# ---------------- LLM工具: shell / file_read / file_write ----------------

SCHEMA_SHELL = {
    "type": "function",
    "function": {
        "name": "shell",
        "description": "[仅可执行模式] 在本机PowerShell执行命令(电脑终端操作)。权限三态: "
                       "allow=直接执行, ask=弹窗请用户确认, deny=拒绝(破坏性命令rm/del/format/shutdown等默认deny)。"
                       "适合: ping/nslookup/nmap/python分析脚本等辅助研判的命令。命令文本会展示给用户确认。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的PowerShell命令"},
                "timeout": {"type": "number", "description": "超时秒数, 默认60"},
            },
            "required": ["command"],
        },
    },
}


def shell(command, timeout=60):
    reason, ok = _gate("shell", command, "执行命令: %s" % command)
    if not ok:
        return {"refused": True, "reason": reason}
    try:  # 钳制LLM可传的超时范围, 防挂死
        timeout = min(max(int(timeout), 5), 300)
    except (TypeError, ValueError):
        timeout = 60
    out, err = run_shell_cmd(command, timeout)
    result = {"command": command, "stdout": out[:12000]}
    if err:
        result["stderr"] = err[:3000]
    if not out and not err:
        result["stdout"] = "(无输出)"
    return result


SCHEMA_FILE_READ = {
    "type": "function",
    "function": {
        "name": "file_read",
        "description": "读取本机文件内容(文本)。敏感路径(.ssh/凭据/密钥库等)被权限规则默认拒绝。"
                       "适合: 读告警导出/pcap文本化/payload文件等研判材料。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "max_chars": {"type": "number", "description": "最大读取字符数, 默认60000"},
            },
            "required": ["path"],
        },
    },
}


def file_read(path, max_chars=60000):
    import os
    path = os.path.expandvars(os.path.expanduser(path))
    reason, ok = _gate("file_read", path, "读取文件: %s" % path)
    if not ok:
        return {"refused": True, "reason": reason}
    try:
        content = open(path, encoding="utf-8", errors="replace").read()
    except Exception as e:
        return {"error": "读取失败: %s" % e}
    if len(content) > max_chars:
        content = content[:max_chars] + " ...[已截断]"
    return {"path": path, "content": content}


SCHEMA_FILE_WRITE = {
    "type": "function",
    "function": {
        "name": "file_write",
        "description": "[仅可执行模式] 写文件(新建/覆盖)。ask权限: 弹窗请用户确认(覆盖已有文件也会确认)。"
                       "适合: 导出研判报告/保存分析结果。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目标文件路径"},
                "content": {"type": "string", "description": "写入内容"},
            },
            "required": ["path", "content"],
        },
    },
}


def file_write(path, content):
    import os
    path = os.path.expandvars(os.path.expanduser(path))
    exists = os.path.exists(path)
    detail = "%s文件: %s" % ("覆盖已有" if exists else "新建", path)
    reason, ok = _gate("file_write", path, detail)
    if not ok:
        return {"refused": True, "reason": reason}
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return {"error": "写入失败: %s" % e}
    return {"success": True, "path": path, "bytes": len(content.encode("utf-8")),
            "overwrote": exists}
