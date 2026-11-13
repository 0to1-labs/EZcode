# -*- coding: utf-8 -*-
"""设备适配器层 —— 把不同厂商的告警源归一化成「统一告警对象」。

为什么需要这一层:
  研判方法论(system.md)与能力标签(caps.py)是设备无关的, 但各家的工具参数、字段名、
  时间格式、严重度枚举完全不同。之前只有 ngsoc/edr 有专用工具, 接第三家设备只能走
  api_<name>(query) 这个万能桩(只回原始文本), 模型拿不到"告警"语义, 方法论接不上。

设计原则:
  1. 适配器只做「取数 + 字段映射」, 不做研判 —— 研判仍由模型按方法论完成。
  2. 通用设备走**声明式 mapping**(纯数据, 绝不执行模型或用户写的代码)。
  3. 复杂设备(ngsoc/edr)复用现有工具模块, 不重写 HTTP 逻辑。
  4. 统一告警对象 + 指纹 → 跨设备同一条告警可聚类合并(组合研判的基础)。

统一告警对象(供 sec_search / sec_detail 与模型消费):
  device/device_kind/project 来源标识; alarm_id/name/severity/severity_label 基本要素;
  ts_ms/time 时间; src_ip/dst_ip/dst_port/host/user/count/status 关键字段;
  raw_ref 下钻凭据(适配器自己知道怎么用); fingerprint 聚类指纹; sources 多设备来源。
"""
import datetime
import hashlib
import json
import re
import ssl
import urllib.parse
import urllib.request

ALARM_FIELDS = ("device", "device_kind", "project", "alarm_id", "name", "severity",
                "severity_label", "ts_ms", "time", "src_ip", "dst_ip", "dst_port",
                "host", "user", "count", "status", "raw_ref", "fingerprint", "sources")

SEV_DEFAULT = {
    "0": "信息", "1": "低", "2": "中", "3": "高", "4": "危急", "5": "危急",
    "info": "信息", "informational": "信息", "low": "低", "medium": "中",
    "high": "高", "critical": "危急", "severe": "危急",
    "提示": "信息", "低危": "低", "中危": "中", "高危": "高", "危急": "危急",
}

_FP_BUCKET_MS = 300000          # 5 分钟时间桶: 同一次攻击在多设备的落点差异在桶内


# ---------------- 基础工具: 取值 / 时间 / 严重度 ----------------

def dig(obj, path):
    """按点路径取值: 'data.list' / 'a.b.c'。空路径返回原对象。"""
    if not path:
        return obj
    cur = obj
    for seg in str(path).split("."):
        if seg == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(seg)
        elif isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except Exception:
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def to_ms(v, fmt=None):
    """把设备时间转成毫秒时间戳。fmt: ms / s / iso8601 / strptime:<pattern>。"""
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v.strip())):
            n = float(v)
            if fmt == "s":
                return int(n * 1000)
            if fmt == "ms":
                return int(n)
            return int(n * 1000) if abs(n) < 1e11 else int(n)
        s = str(v).strip()
        if fmt and fmt.startswith("strptime:"):
            return int(datetime.datetime.strptime(s, fmt.split(":", 1)[1]).timestamp() * 1000)
        iso = s.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(iso)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def fmt_ts(ms):
    if not ms:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def sev_label(v, sev_map=None):
    """严重度归一化成 信息/低/中/高/危急; 认不出就原样返回。"""
    if v is None or v == "":
        return None
    table = dict(SEV_DEFAULT)
    if isinstance(sev_map, dict):
        table.update({str(k).lower(): str(val) for k, val in sev_map.items()})
    key = str(v).strip().lower()
    return table.get(key) or str(v)


def _first_ip(v):
    """源/目 IP 可能是逗号串或数组, 取第一个非空。"""
    if isinstance(v, list):
        return str(v[0]) if v else None
    s = str(v or "").strip()
    if not s or s == "-":
        return None
    return s.split(",")[0].strip() or None


def _int(v):
    try:
        return int(float(v))
    except Exception:
        return None


# ---------------- 统一对象归一化 ----------------

def _pick(raw, spec):
    """字段取值。spec 可以是:
        "srcIp"                                   → 直接路径
        {"path": "level", "map": {"3": "高危"}}   → 取路径后按映射表转换
        {"path": "ts", "format": "ms"}            → 取路径后按时间格式转换
    """
    if spec is None:
        return None
    if isinstance(spec, str):
        return dig(raw, spec)
    if isinstance(spec, dict):
        val = dig(raw, spec.get("path"))
        mp = spec.get("map")
        if isinstance(mp, dict) and val is not None:
            val = mp.get(str(val), val)
        return val
    return None


def normalize(raw, response_mapping, ctx):
    """把一条原始告警按 mapping 归一化成统一对象。

    ctx: {"device": 显示名, "device_kind": 类型, "project": 项目名, "raw_ref": 额外下钻凭据}
    """
    fmap = (response_mapping or {}).get("fields") or {}
    sev_map = (response_mapping or {}).get("severity_map")

    def g(key, default=None):
        spec = fmap.get(key)
        val = _pick(raw, spec) if spec is not None else default
        return val if val not in (None, "") else default

    ts_spec = fmap.get("time")
    ts_fmt = ts_spec.get("format") if isinstance(ts_spec, dict) else None
    ts_ms = to_ms(g("time"), ts_fmt)

    alarm = {
        "device": ctx.get("device"),
        "device_kind": ctx.get("device_kind"),
        "project": ctx.get("project"),
        "alarm_id": g("alarm_id"),
        "name": g("name"),
        "severity": g("severity"),
        "severity_label": sev_label(g("severity"), sev_map),
        "ts_ms": ts_ms,
        "time": fmt_ts(ts_ms),
        "src_ip": _first_ip(g("src_ip")),
        "dst_ip": _first_ip(g("dst_ip")),
        "dst_port": _int(g("dst_port")),
        "host": g("host"),
        "user": g("user"),
        "count": _int(g("count")),
        "status": g("status"),
        "raw_ref": dict(ctx.get("raw_ref") or {}),
    }
    alarm["fingerprint"] = fingerprint(alarm, (response_mapping or {}).get("fingerprint_keys"))
    return alarm


def fingerprint(alarm, keys=None):
    """同一条告警在多家设备的聚类指纹: 源目IP+目端口+规则名(去数字)+时间桶。"""
    keys = keys or ("src_ip", "dst_ip", "dst_port", "name")
    name = re.sub(r"[\s\-_]+", "", str(alarm.get("name") or "").lower())
    name = re.sub(r"\d+", "#", name)[:24]
    bucket = int((alarm.get("ts_ms") or 0) // _FP_BUCKET_MS)
    parts = []
    for k in keys:
        if k == "name":
            parts.append(name)
        else:
            parts.append(str(alarm.get(k) or "").strip().lower())
    parts.append(str(bucket))
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def merge_alarms(alarms, keys=None):
    """按指纹聚类: 同一条告警多设备命中 → 合并成一条, 保留最完整的字段 + sources/evidence_refs。

    输出仍是统一对象列表, 额外字段:
      sources: ["流量侧", "WAF"]        命中的设备名
      evidence_refs: [{device, ...}]   各设备的下钻凭据(供 sec_detail)
      merged: 3                        合并了几条(仅 >1 时出现)
    """
    groups = {}
    for a in alarms:
        fp = a.get("fingerprint") or fingerprint(a, keys)
        a["fingerprint"] = fp
        groups.setdefault(fp, []).append(a)

    out = []
    for fp, items in groups.items():
        def score(x):
            return sum(1 for k in ("alarm_id", "name", "src_ip", "dst_ip", "dst_port",
                                   "host", "user", "count", "status") if x.get(k) not in (None, ""))
        base = dict(max(items, key=score))
        base["sources"] = [it.get("device") for it in items if it.get("device")]
        base["evidence_refs"] = [it.get("raw_ref") for it in items if it.get("raw_ref")]
        if len(items) > 1:
            base["merged"] = len(items)
        times = [it.get("ts_ms") for it in items if it.get("ts_ms")]
        if times:
            base["ts_ms"] = min(times)
            base["time"] = fmt_ts(base["ts_ms"])
        counts = [it.get("count") for it in items if it.get("count")]
        if counts:
            base["count"] = max(counts)
        out.append(base)
    out.sort(key=lambda x: (x.get("ts_ms") or 0), reverse=True)
    return out


# ---------------- 适配器实现 ----------------

class BaseAdapter:
    kind = "base"

    def __init__(self, device, project):
        self.device = device or {}
        self.project = project
        self.name = self.device.get("name") or self.kind

    def probe(self):
        raise NotImplementedError

    def search(self, query):
        raise NotImplementedError

    def detail(self, ref):
        raise NotImplementedError


def _render(tpl, q):
    """把 body/query 模板里的 {since_ms}/{until_ms}/{limit}/... 占位符替换成查询参数。"""
    if isinstance(tpl, dict):
        return {k: _render(v, q) for k, v in tpl.items()}
    if isinstance(tpl, list):
        return [_render(v, q) for v in tpl]
    if isinstance(tpl, str):
        def rep(m):
            key = m.group(1)
            val = q.get(key)
            return "" if val is None else str(val)
        return re.sub(r"\{(\w+)\}", rep, tpl)
    return tpl


class HttpAdapter(BaseAdapter):
    """通用设备: 声明式 mapping 驱动, 不执行任何代码。"""
    kind = "http"

    def __init__(self, device, project):
        super().__init__(device, project)
        self.base = (self.device.get("base_url") or "").rstrip("/")
        self.mapping = self.device.get("mapping") or {}

    # ---- HTTP ----
    def _headers(self):
        auth = self.device.get("auth") or {}
        token = auth.get("token") or self.device.get("api_key") or ""
        kind = (auth.get("type") or ("bearer" if token else "none")).lower()
        headers = {"Accept": "application/json"}
        if kind == "bearer" and token:
            headers["Authorization"] = "Bearer " + token
        elif kind == "header" and token:
            headers[auth.get("key_name") or "X-API-Key"] = token
        elif kind == "query" and token:
            pass    # 由 _url 拼进查询串
        return headers

    def _url(self, path, extra=None):
        url = self.base + (path or "")
        params = dict(extra or {})
        auth = self.device.get("auth") or {}
        if (auth.get("type") or "").lower() == "query":
            token = auth.get("token") or self.device.get("api_key")
            if token:
                params[auth.get("key_name") or "api_key"] = token
        if params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode(params)
        return url

    def _request(self, path, body=None, extra_query=None):
        method = "POST" if body is not None else "GET"
        data = None
        headers = self._headers()
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self._url(path, extra_query), data=data,
                                     headers=headers, method=method)
        ctx = None if self.device.get("verify_ssl") else ssl._create_unverified_context()
        timeout = min(int(self.device.get("timeout") or 30), 90)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            text = r.read().decode("utf-8", "replace")
        try:
            return json.loads(text)
        except Exception:
            return {"_raw_text": text[:20000]}

    # ---- 能力 ----
    def probe(self):
        out = {"device": self.name, "kind": "http", "ok": False, "auth_ok": None,
               "n_alarms": None, "fields": [], "error": None}
        if not self.mapping:
            out["error"] = "未配置字段映射(点「自动适配」让模型生成, 或手填)"
            return out
        try:
            rows = self.search({"limit": 1, "since_ms": _now_ms() - 86400000,
                                "until_ms": _now_ms()})
            out["ok"] = True
            out["auth_ok"] = True
            out["n_alarms"] = len(rows)
            if rows:
                out["fields"] = sorted(k for k, v in rows[0].items()
                                       if v not in (None, "", []) and k not in ("raw_ref", "fingerprint"))
            if not rows:
                out["error"] = "接口可达但返回 0 条告警(可能是时间窗或字段映射不匹配)"
        except Exception as e:
            msg = "%s: %s" % (type(e).__name__, str(e)[:160])
            out["error"] = msg
            out["auth_ok"] = False if ("401" in msg or "403" in msg or "Unauthorized" in msg) else None
        return out

    def search(self, query):
        req = self.mapping.get("request") or {}
        path = _render(req.get("path") or "", query)
        body = _render(req.get("body_template"), query) if req.get("body_template") else None
        extra = _render(req.get("query_template"), query) if req.get("query_template") else None
        data = self._request(path, body if str(req.get("method") or "GET").upper() == "POST" else None, extra)
        resp = self.mapping.get("response") or {}
        lst = dig(data, resp.get("list_path") or "")
        if lst is None and isinstance(data.get("_raw_text"), str):
            raise ValueError("响应不是 JSON 或 list_path 取不到数组, 请检查映射")
        if not isinstance(lst, list):
            raise ValueError("list_path=%r 未取到告警数组(实际类型 %s)"
                             % (resp.get("list_path"), type(lst).__name__))
        limit = _int(query.get("limit")) or len(lst)
        rows = []
        for raw in lst[:max(1, limit)]:
            ctx = {"device": self.name, "device_kind": "http", "project": self.project,
                   "raw_ref": {"device": self.name, "kind": "http",
                               "alarm_id": _pick(raw, (resp.get("fields") or {}).get("alarm_id")),
                               "raw": raw}}
            rows.append(normalize(raw, resp, ctx))
        return rows

    def detail(self, ref):
        d = self.mapping.get("detail") or {}
        path = _render(d.get("path") or "", {"alarm_id": (ref or {}).get("alarm_id") or ""})
        data = self._request(path)
        raw_path = d.get("raw_path")
        return {"device": self.name, "alarm_id": (ref or {}).get("alarm_id"),
                "raw": dig(data, raw_path) if raw_path else data}


class NgsocAdapter(BaseAdapter):
    """ngsoc 适配器: 复用 tools/ngsoc.py 的检索与下钻, 只做字段归一化。"""
    kind = "ngsoc"

    def search(self, query):
        from . import ngsoc
        days = float(query.get("days") or 3)
        if query.get("since_ms"):
            days = max((_now_ms() - float(query["since_ms"])) / 86400000.0, 0.02)
        out = ngsoc.search_alarms(
            site=self.device.get("site") or self.name,
            src_ip=query.get("src_ip"), dst_ip=query.get("dst_ip"),
            alarm_name=query.get("alarm_name"), days=days,
            size=_int(query.get("limit")) or 20, severity=query.get("severity"))
        rows = out.get("alarms") or []
        return [self._norm(r) for r in rows]

    def _norm(self, r):
        ts = r.get("latestTimestamp") or r.get("timestamp")
        ts_ms = to_ms(ts)
        alarm = {
            "device": self.name, "device_kind": "ngsoc", "project": self.project,
            "alarm_id": r.get("uuid"), "name": r.get("name"),
            "severity": r.get("severity"), "severity_label": sev_label(r.get("severity")),
            "ts_ms": ts_ms, "time": r.get("last") or fmt_ts(ts_ms),
            "src_ip": _first_ip(r.get("src")), "dst_ip": _first_ip(r.get("dst")),
            "dst_port": _int(r.get("dstPort")), "host": r.get("host"),
            "user": r.get("user"), "count": _int(r.get("times")), "status": r.get("status"),
            "raw_ref": {"device": self.name, "kind": "ngsoc", "alarm_id": r.get("uuid"),
                        "timestamp": r.get("timestamp"),
                        "latest_timestamp": r.get("latestTimestamp"),
                        "rule_id": r.get("relevantRuleId"),
                        "log_type": r.get("relevantLogsType")},
        }
        alarm["fingerprint"] = fingerprint(alarm)
        return alarm

    def detail(self, ref):
        from . import ngsoc
        site = self.device.get("site") or self.name
        out = {}
        try:
            out["raw"] = ngsoc.get_raw_alarms(
                site=site, alarm_uuid=ref.get("alarm_id"),
                timestamp=ref.get("timestamp"), latest_timestamp=ref.get("latest_timestamp"))
        except Exception as e:
            out["raw_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        if ref.get("rule_id") and ref.get("raw_alarm_uuid"):
            try:
                out["evidence"] = ngsoc.get_evidence(
                    site=site, alarm_uuid=ref.get("alarm_id"),
                    raw_alarm_uuid=ref.get("raw_alarm_uuid"),
                    raw_timestamp=ref.get("raw_timestamp"),
                    rule_id=ref.get("rule_id"), evidence_type=ref.get("evidence_type") or "packet")
            except Exception as e:
                out["evidence_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        return out


class EdrAdapter(BaseAdapter):
    """主机侧: 复用 tools/edr.py。"""
    kind = "edr"

    def search(self, query):
        from . import edr
        out = edr.query(action="list",
                        project=self.device.get("project") or self.name,
                        hours=query.get("hours") or 24,
                        status=query.get("status"))
        rows = out.get("alarms") or []
        return [self._norm(r) for r in rows]

    def _norm(self, r):
        ts_ms = to_ms(r.get("time"), None)
        alarm = {
            "device": self.name, "device_kind": "edr", "project": self.project,
            "alarm_id": r.get("id"), "name": r.get("title"),
            "severity": r.get("severity"), "severity_label": sev_label(r.get("severity")),
            "ts_ms": ts_ms, "time": r.get("time"),
            "src_ip": None, "dst_ip": None, "dst_port": None,
            "host": None, "user": None, "count": None, "status": r.get("status"),
            "raw_ref": {"device": self.name, "kind": "edr", "alarm_id": r.get("id"),
                        "project": self.device.get("project") or self.name},
        }
        alarm["fingerprint"] = fingerprint(alarm)
        return alarm

    def detail(self, ref):
        from . import edr
        return edr.query(action="detail",
                         project=ref.get("project") or self.device.get("project") or self.name,
                         detection_id=ref.get("alarm_id"))


_ADAPTERS = {"http": HttpAdapter, "ngsoc": NgsocAdapter, "edr": EdrAdapter}


def _now_ms():
    return int(datetime.datetime.now().timestamp() * 1000)


def build_adapter(device, project):
    cls = _ADAPTERS.get(str((device or {}).get("kind") or "http").lower())
    return (cls or HttpAdapter)(device, project)


# ---------------- 项目 = 设备组 ----------------

def build_projects(cfg):
    """返回 [{name, devices:[...]}]。优先读 cfg['projects'];
    没有时从旧配置(ngsoc.sites / edr.projects / custom_api.endpoints)内存迁移, 不改盘。"""
    cfg = cfg if isinstance(cfg, dict) else {}
    raw = cfg.get("projects")
    if isinstance(raw, list) and raw:
        out = []
        for p in raw:
            if not isinstance(p, dict) or not (p.get("name") or "").strip():
                continue
            devs = [d for d in (p.get("devices") or []) if isinstance(d, dict)
                    and str(d.get("kind") or "").strip()]
            out.append({"name": str(p["name"]).strip(), "devices": devs})
        if out:
            return out

    devs = []
    for s in ((cfg.get("ngsoc") or {}).get("sites") or []):
        if isinstance(s, dict) and s.get("url"):
            devs.append({"name": s.get("name") or "流量侧", "kind": "ngsoc",
                         "site": s.get("name"), "url": s.get("url"),
                         "token": s.get("token"), "enabled": s.get("enabled", True)})
    for name, (server, token) in (cfg.get("edr_projects") or {}).items():
        if server:
            devs.append({"name": name, "kind": "edr", "project": name,
                         "server": server, "token": token, "enabled": True})
    for e in ((cfg.get("custom_api") or {}).get("endpoints") or []):
        if isinstance(e, dict) and e.get("base_url"):
            devs.append({"name": e.get("name") or "自定义", "kind": "http",
                         "base_url": e.get("base_url"), "api_key": e.get("api_key"),
                         "mapping": e.get("mapping") or {}, "enabled": True})
    return [{"name": "默认项目", "devices": devs}] if devs else []


# ---------------- 自动适配支持: 取样 / 脱敏 / 结构摘要 ----------------

def sample_request(device, path=None, method=None):
    """发一次最小探测请求取原始响应(自动适配用)。不依赖 mapping。"""
    ad = HttpAdapter(device, "__sample__")
    req = (device.get("mapping") or {}).get("request") or {}
    p = path or req.get("path") or ""
    body = None
    if (method or req.get("method") or "GET").upper() == "POST":
        body = req.get("body_template") or {}
    try:
        return {"ok": True, "data": ad._request(p, body)}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def redact(obj, depth=0):
    """结构保留、字符串值替换为 <str>、数字与布尔保留(便于模型判断时间位数与枚举)。
    目的: 把样本发给大模型前隐去主机名/IP/账号等内容, 只暴露字段结构。"""
    if depth > 6:
        return "..."
    if isinstance(obj, dict):
        return {k: redact(v, depth + 1) for k, v in list(obj.items())[:60]}
    if isinstance(obj, list):
        return [redact(v, depth + 1) for v in obj[:5]]
    if isinstance(obj, str):
        return "<str>" if obj else ""
    return obj


def flatten_paths(obj, prefix="", out=None, depth=0):
    """把 JSON 摊平成 '路径: 类型' 列表, 帮助模型确认 list_path 与字段路径。"""
    out = [] if out is None else out
    if depth > 4 or len(out) > 120:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (prefix, k) if prefix else str(k)
            if isinstance(v, (dict, list)):
                out.append("%s: %s(%d)" % (p, type(v).__name__, len(v)))
                flatten_paths(v, p, out, depth + 1)
            else:
                out.append("%s: %s" % (p, type(v).__name__))
    elif isinstance(obj, list):
        if obj:
            flatten_paths(obj[0], prefix + "[]", out, depth + 1)
    return out


def validate_mapping(mapping, sample_data):
    """用真实样本跑一遍 mapping, 判定是否可用。返回 (ok, rows, error)。"""
    try:
        resp = (mapping or {}).get("response") or {}
        lst = dig(sample_data, resp.get("list_path") or "")
        if not isinstance(lst, list):
            return False, [], "list_path=%r 未取到数组(实际 %s)" % (
                resp.get("list_path"), type(lst).__name__)
        if not lst:
            return False, [], "list_path 取到空数组, 无法验证字段映射"
        ctx = {"device": "__sample__", "device_kind": "http", "project": "__sample__",
               "raw_ref": {}}
        rows = [normalize(x, resp, ctx) for x in lst[:3]]
        good = [r for r in rows if r.get("name") or r.get("ts_ms")]
        if not good:
            return False, rows, "映射结果缺少 name 与 time, 至少要对上其中一个"
        return True, rows, None
    except Exception as e:
        return False, [], "%s: %s" % (type(e).__name__, str(e)[:160])
