# -*- coding: utf-8 -*-
"""LLM 客户端 (OpenAI 兼容接口, 流式SSE)
- 思考流: reasoning_content / reasoning / thinking 三种字段名自适应
- tool_calls 分片聚合(按index拼接)
- 服务端不支持stream时自动降级非流式
- 超时: 连接15s / 流读60s(停滞快速报错), 不会永久挂起
"""
import json
import time
import requests

REASON_KEYS = ("reasoning_content", "reasoning", "thinking")
PLACEHOLDER_MARKS = ("填", "你的", "在这里")


def check_key(profile, name):
    key = (profile.get("api_key") or "").strip()
    if key and any(m in key for m in PLACEHOLDER_MARKS):
        raise RuntimeError("profile %r 的 api_key 还是占位符, 请在 devices.json 填真实key" % name)
    return key


def _chat_url(profile):
    url = profile["base_url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    return url


def _body(profile, messages, tools, stream):
    body = {"model": profile.get("model", "deepseek-chat"), "messages": messages}
    if tools:
        body["tools"] = tools
    temp = profile.get("temperature")
    if temp is not None:
        body["temperature"] = temp
    out = profile.get("output")
    if out:
        body["max_tokens"] = int(out)
    if stream:
        body["stream"] = True
    return body


def _post(profile, profile_name, body, stream):
    headers = {"Content-Type": "application/json"}
    key = check_key(profile, profile_name)
    if key:
        headers["Authorization"] = "Bearer " + key
    return requests.post(_chat_url(profile), headers=headers, json=body, stream=stream,
                         timeout=(15, 60 if stream else 300),
                         verify=bool(profile.get("verify_ssl", True)))


class Cancelled(Exception):
    pass


def stream_chat(profile, profile_name, messages, tools=None, cancel=None,
                on_reason=None, on_content=None):
    """流式对话。返回 (assistant_msg, reason_chars, elapsed)。
    on_reason/on_content: 增量回调(参数=已累计全量文本)。cancel.set() 抛 Cancelled。"""
    t0 = time.time()
    body = _body(profile, messages, tools, stream=True)
    try:
        resp = _post(profile, profile_name, body, stream=True)
    except requests.RequestException as e:
        raise RuntimeError("连接LLM失败: %s" % str(e)[:200])
    if resp.status_code != 200:
        txt = (resp.text or "")[:300]
        if "stream" in txt.lower():          # 服务端不支持流式 → 降级
            resp.close()
            return _nonstream(profile, profile_name, messages, tools, t0)
        raise RuntimeError("LLM[%s] HTTP %s: %s" % (profile_name, resp.status_code, txt))

    reason, content, tc_agg = [], [], {}
    try:
        for raw in resp.iter_lines(chunk_size=None):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if not raw:
                continue
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                j = json.loads(data)
            except Exception:
                continue
            ch = (j.get("choices") or [{}])[0]
            delta = ch.get("delta") or {}
            for rk in REASON_KEYS:
                if delta.get(rk):
                    reason.append(delta[rk])
                    if on_reason:
                        on_reason("".join(reason))
                    break
            if delta.get("content"):
                content.append(delta["content"])
                if on_content:
                    on_content("".join(content))
            if delta.get("tool_calls"):
                for tc in delta["tool_calls"]:
                    i = tc.get("index", 0)
                    a = tc_agg.setdefault(i, {"id": "", "name": "", "args": ""})
                    if tc.get("id"):
                        a["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        a["name"] = fn["name"]
                    if fn.get("arguments"):
                        a["args"] += fn["arguments"]
    except requests.RequestException as e:
        raise RuntimeError("LLM流中断: %s" % str(e)[:150])
    finally:
        try:
            resp.close()
        except Exception:
            pass

    msg = {"role": "assistant", "content": "".join(content) if content else None}
    if tc_agg:
        msg["tool_calls"] = [{"id": v["id"] or ("call_%d" % i), "type": "function",
                              "function": {"name": v["name"], "arguments": v["args"]}}
                             for i, v in sorted(tc_agg.items())]
    return msg, len("".join(reason)), time.time() - t0


def _nonstream(profile, profile_name, messages, tools, t0):
    body = _body(profile, messages, tools, stream=False)
    resp = _post(profile, profile_name, body, stream=False)
    if resp.status_code != 200:
        raise RuntimeError("LLM[%s] HTTP %s: %s" % (profile_name, resp.status_code,
                                                   (resp.text or "")[:300]))
    msg = resp.json()["choices"][0]["message"]
    full = msg.get("content") or ""
    return msg, 0, time.time() - t0


def oneshot(profile, profile_name, system, user, max_chars=4000):
    """一次性调用(会话标题生成等, 非流式)。"""
    body = _body(profile, [{"role": "system", "content": system},
                           {"role": "user", "content": user}], None, stream=False)
    try:
        resp = _post(profile, profile_name, body, stream=False)
    except requests.RequestException as e:
        raise RuntimeError("连接LLM失败: %s" % str(e)[:150])
    if resp.status_code != 200:
        raise RuntimeError("LLM HTTP %s" % resp.status_code)
    msg = resp.json()["choices"][0]["message"]
    return (msg.get("content") or "")[:max_chars]
