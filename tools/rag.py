# -*- coding: utf-8 -*-
"""RAG 知识库检索工具(配置驱动, 设备无关)。

**对接契约(最小要求)**: 你只需要一个 HTTP 检索接口, 加一个查询字段名。
  POST {"<query_field>": "问题"}  或  GET <url>?<query_field>=问题
响应可以是 JSON, 也可以是纯文本; JSON 会自动提取正文(兼容 data/results/documents/hits/items 等常见结构)。
**不需要模型支持 RAG** —— 检索在本地做, 片段塞进上下文, 任何 OpenAI 兼容模型都能用;
只有厂商托管的"文件搜索/知识库"服务才需要模型侧支持, 那种情况直接当普通 API 接入即可。

devices.json 配置节:
  "rag": {
    "enabled": true,
    "url": "http://kb.example.com/search",   # 检索接口地址(必填)
    "api_key": "",                              # 可选, Bearer
    "method": "POST",                           # POST(默认) / GET
    "query_field": "query",                     # 查询字段名(默认 query)
    "send_top_k": false,                        # 是否附带 top_k 字段(默认不发, 兼容严格接口)
    "timeout": 30,
    "verify_ssl": false,                        # 内网自签证书默认不校验
    "capabilities": ["knowledge"]               # 能力标签, 进能力清单/校验门
  }
配置存在即注册工具 rag_search, 删掉配置即注销——设置界面改完热生效。"""
import json
import ssl
import time
import urllib.parse
import urllib.request

_TOOL_NAME = "rag_search"
_SCHEMA = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": "检索外部 RAG 知识库(向量/全文检索服务: 产品文档、制度规范、FAQ、内部手册)。"
                       "调用时机: ①本地判据库 kb_search 没覆盖到的知识点 ②需要查产品/设备文档、配置说明 "
                       "③制度、流程、规范类问题 ④用户明确说'查知识库/查文档'。"
                       "注意: 误报族与研判判据优先用 kb_search(带判据编号), 不要用本工具替代。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索问题/关键词(自然语言即可)"},
        }, "required": ["query"]},
    },
}

_state = {"registered": False, "cfg": None}


def configure(cfg):
    """注册/注销 rag_search(按配置存在与否)。可重复调用(设置保存后热生效)。"""
    import caps
    from tools import TOOLS, HANDLERS
    rag = cfg.get("rag") if isinstance(cfg, dict) else None
    valid = bool(rag and rag.get("enabled") and rag.get("url"))
    if valid:
        _state["cfg"] = dict(rag)
        if not _state["registered"]:
            TOOLS.append(_SCHEMA)
            HANDLERS[_TOOL_NAME] = rag_search
            caps.CAPABILITIES[_TOOL_NAME] = list(rag.get("capabilities") or ["knowledge"])
            _state["registered"] = True
        else:
            caps.CAPABILITIES[_TOOL_NAME] = list(rag.get("capabilities") or ["knowledge"])
    else:
        unregister()


def unregister():
    import caps
    from tools import TOOLS, HANDLERS
    if _state["registered"]:
        TOOLS[:] = [t for t in TOOLS if t["function"]["name"] != _TOOL_NAME]
        HANDLERS.pop(_TOOL_NAME, None)
        caps.CAPABILITIES.pop(_TOOL_NAME, None)
        _state.update({"registered": False, "cfg": None})


def _http(cfg, query):
    """发一次检索请求, 返回响应原文。SSRF说明: 端点是用户在设置里登记的可信目标。"""
    timeout = min(int(cfg.get("timeout") or 30), 60)
    headers = {"Accept": "application/json"}
    key = (cfg.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    field = str(cfg.get("query_field") or "query").strip() or "query"
    params = {field: str(query or "")}
    if cfg.get("send_top_k"):
        params["top_k"] = int(cfg.get("top_k") or 5)
    if (cfg.get("method") or "POST").upper() == "GET":
        sep = "&" if "?" in cfg["url"] else "?"
        req = urllib.request.Request(cfg["url"] + sep + urllib.parse.urlencode(params),
                                     headers=headers)
    else:
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(cfg["url"],
                                     data=json.dumps(params, ensure_ascii=False).encode("utf-8"),
                                     headers=headers, method="POST")
    ctx = ssl.create_default_context() if cfg.get("verify_ssl") else ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return (r.read().decode("utf-8", "replace") or "")


def _extract(text):
    """尽量从响应里抽出文档正文; 认不出就原样返回(模型能读)。"""
    s = (text or "").strip()
    if not s.startswith(("{", "[")):
        return s
    try:
        data = json.loads(s)
    except Exception:
        return s
    chunks = []

    def walk(node, depth=0):
        if depth > 4 or len(chunks) > 30:
            return
        if isinstance(node, str):
            t = node.strip()
            if len(t) >= 8:
                chunks.append(t)
        elif isinstance(node, list):
            for x in node[:20]:
                walk(x, depth + 1)
        elif isinstance(node, dict):
            for k in ("content", "text", "chunk", "page_content", "answer",
                      "snippet", "body", "document", "summary"):
                if isinstance(node.get(k), str):
                    walk(node[k], depth + 1)
                    return
            for k in ("data", "results", "documents", "hits", "items", "records", "list", "docs"):
                if k in node:
                    walk(node[k], depth + 1)
                    return
            for v in list(node.values())[:6]:
                walk(v, depth + 1)

    walk(data)
    return "\n\n---\n\n".join(chunks)[:16000] if chunks else s


def rag_search(query, **_ignored):
    cfg = _state.get("cfg")
    if not cfg:
        return {"error": "RAG知识库未配置, 请在 设置 → RAG知识库 填写检索接口地址"}
    try:
        text = _http(cfg, str(query or ""))
    except Exception as e:
        return {"error": "检索失败: %s: %s" % (type(e).__name__, str(e)[:200]),
                "hint": "检查设置里 RAG 接口地址 / 鉴权 / 网络可达性; 自签证书可关掉「校验SSL」"}
    body = _extract(text)
    return {"query": query, "results": body[:16000] or "(空响应)",
            "empty": not body.strip()}


def rag_test(cfg=None):
    """设置页「测试检索」: 用当前表单配置发一次真实请求(不落盘), 返回耗时/字符数/首段。"""
    c = dict(cfg or _state.get("cfg") or {})
    if not c.get("url"):
        return {"ok": False, "error": "未填写检索接口地址"}
    t0 = time.time()
    try:
        text = _http(c, "连通性测试")
    except Exception as e:
        msg = "%s: %s" % (type(e).__name__, str(e)[:200])
        hint = "检查地址/鉴权; 自签证书请取消「校验SSL」"
        if "401" in msg or "403" in msg:
            hint = "鉴权失败: 检查 API Key 或字段名"
        elif "404" in msg:
            hint = "路径不存在: 检查接口地址后缀"
        return {"ok": False, "error": msg, "hint": hint}
    ms = int((time.time() - t0) * 1000)
    body = _extract(text)
    return {"ok": True, "ms": ms, "chars": len(text),
            "preview": (body or "(空响应)")[:300],
            "empty": not (body or "").strip()}
