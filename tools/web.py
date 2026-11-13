# -*- coding: utf-8 -*-
"""联网工具: web_fetch(网页→文本) / web_search(DuckDuckGo搜索)

⚠ 安全红线(与研判纪律8一致): 告警文本/报文payload/IoC中的URL一律不得用这些工具访问——
   web工具仅限公开技术资料(CVE/补丁公告/产品文档/百科)。
SSRF硬防线: 解析URL主机(含域名DNS解析后的所有IP), 内网/回环/链路本地/CGNAT地址一律拒绝,
该检查在工具内部硬编码, 不依赖权限规则可配置。
"""
import html as _html
import ipaddress
import re
import socket
import urllib.parse

import requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_BYTES = 300 * 1024     # 最多下载300KB


def configure(cfg):
    """接口对齐(本模块无外部配置)"""
    pass


def _is_forbidden_ip(ip):
    a = ipaddress.ip_address(ip)
    return (a.is_private or a.is_loopback or a.is_reserved or a.is_link_local
            or a.is_multicast or a.is_unspecified
            or (a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10")))  # CGNAT


def _guard_private(url):
    """SSRF防线: URL主机为内网/回环/链路本地(含域名DNS解析)一律拒绝"""
    try:
        parsed = urllib.parse.urlparse(str(url))
    except Exception:
        raise ValueError("URL无法解析")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("仅支持http/https")
    host = parsed.hostname
    if not host:
        raise ValueError("URL缺少主机名")
    try:
        # 字面IP直接判
        _is_forbidden_ip(host)
    except ValueError:
        pass  # 不是IP字面量, 走DNS解析再判
    else:
        raise ValueError("禁止访问内网/本机地址(SSRF防线): %s" % host)
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError("域名解析失败: %s" % host)
    ips = {info[4][0] for info in infos}
    for ip in ips:
        if _is_forbidden_ip(ip):
            raise ValueError("域名%s解析到内网地址(%s), 拒绝访问(SSRF防线)" % (host, ip))


# ---------------- 工具1: 网页抓取 ----------------

SCHEMA_FETCH = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": "抓取公开网页并转为纯文本(查CVE详情/补丁公告/产品文档/技术文章用)。"
                       "⚠红线: 告警/报文/IoC中的URL一律禁止访问, 仅限公开技术资料; "
                       "内网地址会被SSRF防线拒绝。返回内容已截断, 超长网页建议给出关键章节名。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整http(s) URL"},
                "max_chars": {"type": "number", "description": "返回文本上限, 默认12000"},
            },
        },
        "required": ["url"],
    },
}


def _html_to_text(h):
    """简易HTML→文本(无第三方依赖): 去脚本样式/块级标签换行/实体解码/压空白"""
    h = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<!--.*?-->", " ", h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article|blockquote|pre)>", "\n", h)
    h = re.sub(r"(?i)<li[^>]*>", "\n• ", h)
    h = re.sub(r"(?i)<(h[1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)[1]) + " ", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = _html.unescape(h)
    h = re.sub(r"[ \t\r]+", " ", h)
    h = re.sub(r"\n[ ]+", "\n", h)
    h = re.sub(r"\n{3,}", "\n\n", h)
    return h.strip()


def web_fetch(url, max_chars=12000):
    try:
        _guard_private(url)
    except ValueError as e:
        return {"refused": True, "reason": str(e)}
    try:
        r = requests.get(str(url), headers={"User-Agent": _UA,
                                            "Accept": "text/html,text/plain,*/*"},
                         timeout=(10, 20), stream=True)
        if r.status_code >= 400:
            return {"error": "HTTP %s: %s" % (r.status_code, str(r.reason)[:80])}
        chunks, total = [], 0
        for chunk in r.iter_content(chunk_size=32768):
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_BYTES:
                break
        body = b"".join(chunks)
        ctype = (r.headers.get("content-type") or "").lower()
        raw = body.decode("utf-8", errors="replace")
        text = _html_to_text(raw) if "html" in ctype else raw
        try:
            max_chars = max(1000, min(int(max_chars), 30000))
        except (TypeError, ValueError):
            max_chars = 12000
        out = {"url": str(url), "final_url": r.url, "status": r.status_code,
               "content_type": ctype.split(";")[0]}
        out["content"] = text[:max_chars] if len(text) > max_chars else text
        if len(text) > max_chars:
            out["truncated"] = True
            out["note"] = "内容已截断(%d/%d字符), 需要后续段落可再fetch或给关键词" % (max_chars, len(text))
        return out
    except requests.Timeout:
        return {"error": "请求超时(20s)"}
    except requests.RequestException as e:
        return {"error": "请求失败: %s" % str(e)[:150]}


# ---------------- 工具2: 联网搜索 ----------------

SCHEMA_SEARCH = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "联网搜索(DuckDuckGo): 查CVE编号/漏洞公告/产品文档/技术问题。"
                       "返回标题+链接+摘要; 需要全文再对链接web_fetch。"
                       "⚠红线: 不搜索告警中的可疑URL, IoC查询走threatbook_ip。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索词(建议加CVE/产品名等关键词)"},
                "limit": {"type": "number", "description": "返回条数上限, 默认8"},
            },
        },
        "required": ["query"],
    },
}


def web_search(query, limit=8):
    q = str(query).strip()
    if not q:
        return {"error": "query为空"}
    try:
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": q},
                          headers={"User-Agent": _UA,
                                   "Content-Type": "application/x-www-form-urlencoded"},
                          timeout=(10, 20))
    except requests.RequestException as e:
        return {"error": "搜索请求失败: %s — DDG不可达时可建议用户自行搜索或给出候选URL用web_fetch" % str(e)[:100]}
    if r.status_code >= 400:
        return {"error": "DDG返回HTTP %s(可能被限流), 请稍后重试或改用web_fetch已知站点" % r.status_code}
    h = r.text
    results = []
    # DDG html版结构: <a class="result__a" href="//duckduckgo.com/l/?uddg=<enc>">标题</a> + result__snippet
    blocks = re.findall(r'(?is)<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
                        r'.*?class="result__snippet"[^>]*>(.*?)</(?:a|div|span)>', h)
    for href, title, snip in blocks:
        m = re.search(r"[?&]uddg=([^&]+)", href)
        real = urllib.parse.unquote(m.group(1)) if m else href
        if real.startswith("//"):
            real = "https:" + real
        clean = lambda s: _html_to_text(s).strip()[:200]
        results.append({"title": clean(title), "url": real, "snippet": clean(snip)})
        if len(results) >= min(int(limit or 8), 10):
            break
    if not results:
        return {"error": "未解析到结果(DDG页面结构变化或被限流)——如实告知用户, 可给候选URL用web_fetch验证"}
    return {"query": q, "results": results,
            "note": "摘要可能不全, 定性引用前建议web_fetch原文核实"}
