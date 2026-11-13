# -*- coding: utf-8 -*-
"""EZcode示例插件: 报文/载荷解码工具(base64/URL)
安装方式: 放入 plugins/ 目录即生效(本文件已内置)。也是插件开发模板。

插件标准接口(全部可选, 但至少提供TOOLS):
    TOOLS        = [{"schema": OpenAI函数schema, "handler": callable, "exec": False}]
                   exec=True 则仅在可执行模式(Tab)下开放
    PLUGIN_PROMPT = 附加给系统提示词的说明文字
    on_load(cfg)  = 启动钩子
"""
import base64
import binascii
import re
import urllib.parse

_TOOLS = []


def _hexdump(raw, limit=1024):
    head = raw[:limit]
    lines = []
    for i in range(0, len(head), 16):
        chunk = head[i:i + 16]
        hexpart = " ".join("%02x" % b for b in chunk)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("%04x  %-47s  %s" % (i, hexpart, asciipart))
    return "\n".join(lines)


def b64_decode(data):
    """base64解码: 自动清洗空白, 输出hexdump+可打印字符串(研判payload常用)"""
    s = re.sub(r"\s+", "", str(data or ""))
    if not s:
        return {"error": "data为空"}
    # 容错padding
    if len(s) % 4:
        s += "=" * (4 - len(s) % 4)
    try:
        raw = base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError) as e:
        return {"error": "base64解码失败: %s" % e}
    strings = re.findall(rb"[\x20-\x7e]{4,}", raw[:4096])
    return {"bytes_total": len(raw),
            "hexdump": _hexdump(raw),
            "ascii_strings": [x.decode("ascii", "replace")[:120] for x in strings[:40]],
            "note": "shown_bytes=min(bytes_total,1024); 解码结果仅供证据分析"}


def url_decode(s):
    """URL解码: 解码%XX和+号, 研判URI参数用"""
    text = str(s or "")
    if not text:
        return {"error": "s为空"}
    try:
        decoded = urllib.parse.unquote_plus(text)
    except Exception as e:
        return {"error": "解码失败: %s" % e}
    return {"input_len": len(text), "decoded": decoded[:8000]}


TOOLS = [
    {"schema": {
        "type": "function",
        "function": {
            "name": "b64_decode",
            "description": "base64解码(自动清洗空白/容错padding), 输出hexdump+可打印字符串。研判报文/日志/告警详情页payload中的base64内容时使用。",
            "parameters": {"type": "object",
                           "properties": {"data": {"type": "string", "description": "base64字符串(可含换行/空格)"}},
                           "required": ["data"]},
        },
    }, "handler": b64_decode, "exec": False},
    {"schema": {
        "type": "function",
        "function": {
            "name": "url_decode",
            "description": "URL解码(%XX与+号)。研判注入类URI参数/编码混淆时使用。",
            "parameters": {"type": "object",
                           "properties": {"s": {"type": "string", "description": "待解码字符串"}},
                           "required": ["s"]},
        },
    }, "handler": url_decode, "exec": False},
]

PLUGIN_PROMPT = ("扩展解码工具可用: 遇到告警payload/URI/日志中的base64或URL编码内容, "
                 "优先用b64_decode/url_decode解码后再定性(不要凭编码形态猜测内容)。")
