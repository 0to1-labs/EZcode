# -*- coding: utf-8 -*-
"""自定义内部系统API工具(配置驱动, 只读查询)。
devices.json 配置节:
  "custom_api": {
    "endpoints": [
      {
        "name": "cmdb",                    # 工具注册为 api_cmdb
        "description": "查CMDB资产, query填主机名或IP",
        "base_url": "https://cmdb.example.com/api/query",
        "api_key": "",                      # 可选, Bearer
        "method": "GET",                    # GET: query拼URL查询串 / POST: {"query": query}
        "capabilities": ["asset"],          # 能力标签: asset/intel/knowledge/host... 会进能力清单与校验门
        "timeout": 30,
        "verify_ssl": false
      }
    ]
  }
每个端点注册一个 api_<name> 只读查询工具(设置保存后热生效)。
安全边界: base_url 由用户在设置里登记(视为可信目标); path不可注入, 仅query参数可控。"""
import json
import ssl

import urllib.request
import urllib.parse

_state = {"registered": {}}      # name -> schema


def _mk_schema(name, desc):
    return {
        "type": "function",
        "function": {
            "name": "api_%s" % name,
            "description": "[自定义接入] %s" % (desc or ("查询内部系统 %s" % name)),
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "查询参数(按该系统的接口语义填写)"},
            }, "required": ["query"]},
        },
    }


def configure(cfg):
    """按配置重注册全部 api_<name> 工具(先全注销再逐个注册, 增删改都正确)。"""
    unregister()
    from tools import TOOLS, HANDLERS
    import caps
    eps = []
    if isinstance(cfg, dict):
        node = cfg.get("custom_api")
        if isinstance(node, dict):
            eps = [e for e in (node.get("endpoints") or []) if isinstance(e, dict)]
    for e in eps:
        name = str(e.get("name") or "").strip().lower().replace(" ", "_")
        if not name or not e.get("base_url"):
            continue
        schema = _mk_schema(name, e.get("description"))
        TOOLS.append(schema)
        HANDLERS[schema["function"]["name"]] = _handler(e)
        caps.CAPABILITIES[schema["function"]["name"]] = list(
            e.get("capabilities") or [])
        _state["registered"][name] = e


def unregister():
    from tools import TOOLS, HANDLERS
    import caps
    for name in list(_state["registered"]):
        fn = "api_%s" % name
        TOOLS[:] = [t for t in TOOLS if t["function"]["name"] != fn]
        HANDLERS.pop(fn, None)
        caps.CAPABILITIES.pop(fn, None)
    _state["registered"] = {}


def _handler(ep):
    def call(query, **_ignored):
        timeout = min(int(ep.get("timeout") or 30), 60)
        headers = {"Accept": "application/json"}
        key = (ep.get("api_key") or "").strip()
        if key:
            headers["Authorization"] = "Bearer " + key
        method = (ep.get("method") or "GET").upper()
        url = ep["base_url"]
        try:
            if method == "POST":
                body = json.dumps({"query": query}).encode("utf-8")
                headers["Content-Type"] = "application/json"
                req = urllib.request.Request(url, data=body, headers=headers,
                                             method="POST")
            else:
                sep = "&" if "?" in url else "?"
                req = urllib.request.Request(
                    "%s%s%s" % (url, sep, urllib.parse.urlencode({"query": query})),
                    headers=headers)
            if ep.get("verify_ssl"):
                ctx = ssl.create_default_context()
            else:
                ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                text = (r.read().decode("utf-8", "replace") or "")[:16000]
        except Exception as e:
            return {"error": "查询失败: %s: %s" % (type(e).__name__, str(e)[:200]),
                    "hint": "检查设置里该接入的地址/鉴权/网络可达性"}
        return {"query": query, "results": text or "(空响应)"}
    call.__name__ = "api_%s" % (ep.get("name") or "custom")
    return call
