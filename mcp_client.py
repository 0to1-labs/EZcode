# -*- coding: utf-8 -*-
"""EZcode 手搓MCP客户端 (Model Context Protocol, 同步实现无SDK依赖)
课程对齐: P13 MCP fundamentals —— 从wire envelope开始造, 不引SDK。

支持两种传输:
  stdio:  子进程 + 换行分隔的JSON-RPC(reader线程+队列匹配id)
  http:   Streamable HTTP (POST JSON-RPC, 兼容SSE响应, 管理会话头)

协议流程: initialize -> notifications/initialized -> tools/list -> tools/call
"""
import json
import os
import subprocess
import sys
import threading
import queue
import time

import requests

PROTO_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "EZcode", "version": "1.2"}


def _sanitize_tool_name(server_name, tool_name):
    """mcp__<server>__<tool>, 清洗非法字符, OpenAI函数名约束[a-zA-Z0-9_-]{,64}"""
    raw = "mcp__%s__%s" % (server_name, tool_name)
    cleaned = "".join(c if (c.isalnum() or c in "_-") else "_" for c in raw)
    return cleaned[:64]


class McpError(Exception):
    pass


class _LineQueueReader(threading.Thread):
    """读子进程stdout行入队列(处理通知/杂音, 按id匹配响应)"""

    def __init__(self, pipe, srv):
        super().__init__(daemon=True)
        self.pipe = pipe
        self.srv = srv
        self.q = queue.Queue()

    def run(self):
        for line in iter(self.pipe.readline, ""):
            line = line.strip()
            if not line:
                continue
            try:
                self.q.put(json.loads(line))
            except Exception:
                pass  # 非JSON行(服务日志)忽略
        self.q.put(None)  # EOF哨兵


class McpStdioServer:
    """stdio传输的MCP server"""

    def __init__(self, name, command, env=None, call_timeout=180):
        self.name = name
        self.call_timeout = call_timeout
        cmd = list(command)
        # Windows: .cmd/.bat 需经 cmd /c 启动
        if os.name == "nt" and cmd and cmd[0].lower().endswith((".cmd", ".bat")):
            cmd = ["cmd", "/c"] + cmd
        # 不弹黑框(CREATE_NO_WINDOW): stdio MCP子进程(npx/node等控制台程序)启动时闪cmd窗
        popen_flags = 0x08000000 if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            env=env, creationflags=popen_flags)
        self._id = 0
        self._reader = _LineQueueReader(self.proc.stdout, self)
        self._reader.start()
        self._lock = threading.Lock()
        self._handshake()

    def _send(self, obj):
        try:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()
        except Exception as e:
            raise McpError("MCP[%s] stdin写入失败: %s" % (self.name, e))

    def _next_id(self):
        self._id += 1
        return self._id

    def _wait_response(self, rid, timeout):
        deadline = time.time() + timeout
        pending = []       # 非本请求的响应(如乱序到达的其他id), 回灌队列防丢
        result = None
        while time.time() < deadline:
            try:
                msg = self._reader.q.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if msg is None:
                break  # 进程退出
            mid = msg.get("id")
            if mid == rid and ("result" in msg or "error" in msg):
                result = msg
                break
            if mid is None:
                continue  # JSON-RPC通知, 丢弃
            if "method" in msg:
                # server->client请求(如sampling): 回method not found, 防对端挂等
                self._send({"jsonrpc": "2.0", "id": mid,
                            "error": {"code": -32601, "message": "EZcode不支持该服务端请求"}})
            else:
                pending.append(msg)  # 其他请求的响应, 回灌
        for m in pending:
            self._reader.q.put(m)
        if result is None:
            raise McpError("MCP[%s] 响应超时/进程退出 (id=%s)" % (self.name, rid))
        if "error" in result:
            raise McpError("MCP[%s] 错误: %s" % (self.name, json.dumps(result["error"])[:200]))
        return result.get("result")

    def _handshake(self):
        rid = self._next_id()
        self._send({"jsonrpc": "2.0", "id": rid, "method": "initialize",
                    "params": {"protocolVersion": PROTO_VERSION,
                               "capabilities": {},
                               "clientInfo": CLIENT_INFO}})
        self._wait_response(rid, 30)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, method, params=None, timeout=None):
        with self._lock:
            rid = self._next_id()
            req = {"jsonrpc": "2.0", "id": rid, "method": method}
            if params is not None:
                req["params"] = params
            self._send(req)
            return self._wait_response(rid, timeout or self.call_timeout)

    def list_tools(self):
        result = self.call("tools/list", {})
        return (result or {}).get("tools") or []

    def call_tool(self, tool_name, arguments):
        result = self.call("tools/call", {"name": tool_name, "arguments": arguments or {}})
        return _extract_content(result)

    def close(self):
        try:
            self.proc.terminate()
        except Exception:
            pass


class McpHttpServer:
    """Streamable HTTP传输的MCP server (POST JSON-RPC, 兼容JSON与SSE响应)"""

    def __init__(self, name, url, headers=None, call_timeout=120):
        self.name = name
        self.url = url
        self.extra_headers = dict(headers or {})
        self.call_timeout = call_timeout
        self._id = 0
        self._session_id = None
        self._lock = threading.Lock()
        self._handshake()

    def _headers(self):
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        h.update(self.extra_headers)
        return h

    def _post(self, payload, timeout):
        r = requests.post(self.url, headers=self._headers(), json=payload, timeout=timeout)
        if r.status_code == 404 and self._session_id:
            # 会话过期: 重新握手一次
            self._session_id = None
            self._handshake()
            r = requests.post(self.url, headers=self._headers(), json=payload, timeout=timeout)
        if r.status_code >= 400:
            raise McpError("MCP[%s] HTTP %s: %s" % (self.name, r.status_code, r.text[:200]))
        sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
        if sid and not self._session_id:
            self._session_id = sid
        ctype = (r.headers.get("content-type") or "").lower()
        if "text/event-stream" in ctype:
            return self._parse_sse(r, payload.get("id"))
        try:
            return r.json()
        except Exception:
            raise McpError("MCP[%s] 非JSON响应: %s" % (self.name, r.text[:200]))

    def _parse_sse(self, resp, rid):
        """SSE响应: 逐data:行解析, 直到匹配id"""
        for raw in resp.iter_lines(chunk_size=None):
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                msg = json.loads(data)
            except Exception:
                continue
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                return msg
        raise McpError("MCP[%s] SSE流中未找到id=%s的响应" % (self.name, rid))

    def _next_id(self):
        self._id += 1
        return self._id

    def _handshake(self):
        rid = self._next_id()
        resp = self._post({"jsonrpc": "2.0", "id": rid, "method": "initialize",
                           "params": {"protocolVersion": PROTO_VERSION,
                                      "capabilities": {},
                                      "clientInfo": CLIENT_INFO}}, 30)
        if "error" in resp:
            raise McpError("MCP[%s] initialize错误: %s" % (self.name, json.dumps(resp["error"])[:200]))
        requests.post(self.url, headers=self._headers(), timeout=30,
                     json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _rpc(self, method, params=None, timeout=None):
        with self._lock:
            rid = self._next_id()
            req = {"jsonrpc": "2.0", "id": rid, "method": method}
            if params is not None:
                req["params"] = params
            resp = self._post(req, timeout or self.call_timeout)
            if resp.get("id") != rid and ("result" in resp or "error" in resp):
                pass  # id对不上但结构完整, 容忍
            if "error" in resp:
                raise McpError("MCP[%s] 错误: %s" % (self.name, json.dumps(resp["error"])[:200]))
            return resp.get("result")

    def list_tools(self):
        return (self._rpc("tools/list", {}) or {}).get("tools") or []

    def call_tool(self, tool_name, arguments):
        return _extract_content(self._rpc("tools/call", {"name": tool_name,
                                                         "arguments": arguments or {}}))

    def close(self):
        pass


def _extract_content(result):
    """tools/call结果 -> {content: str} 或 {error}"""
    if not isinstance(result, dict):
        return {"content": str(result)[:8000]}
    if result.get("isError"):
        texts = [c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text"]
        return {"error": ("; ".join(texts) or "MCP工具返回isError")[:800]}
    texts, structured = [], None
    for c in result.get("content") or []:
        if c.get("type") == "text":
            texts.append(c.get("text", ""))
    if result.get("structuredContent") is not None:
        structured = result["structuredContent"]
    if structured is not None:
        try:
            return {"content": json.dumps(structured, ensure_ascii=False, default=str)[:16000]}
        except Exception:
            pass
    return {"content": ("\n".join(texts) or json.dumps(result, ensure_ascii=False, default=str))[:16000]}


def connect_server(spec):
    """按devices.json配置建连: {name, type: stdio|http, command|url, ...}"""
    stype = (spec.get("type") or "http").lower()
    if stype in ("http", "streamable-http", "streamable_http", "sse"):
        return McpHttpServer(spec["name"], spec["url"], spec.get("headers"))
    if stype == "stdio":
        return McpStdioServer(spec["name"], spec.get("command") or [],
                              env=_env_with(spec.get("env")))
    raise McpError("未知MCP类型 %r" % stype)


def _env_with(extra):
    if not extra:
        return None
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in extra.items()})
    return env
