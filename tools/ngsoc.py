# -*- coding: utf-8 -*-
"""告警平台六站告警工具: 告警检索 / 原始告警下钻 / 报文证据下钻(内置限频+packet_data自动hexdump解码)

站点配置原位复用 skills/ngsoc-triage/sites.json, 不复制token。
新站(R4.15.x)走 POST /api/v1/alarms/list-data, 旧站(R4.14.x)走 POST /api/v1/alarms/list。
"""
import json, ssl, time, re, base64, datetime, urllib.request, urllib.parse, threading
import concurrent.futures
import functools
from . import permissions as _perm


@functools.lru_cache(maxsize=2)
def _mk_ctx(verify):
    """按 verify 缓存 SSL 上下文。
    create_default_context() 要枚举系统根证书(约 55ms/次), 启动期连建数次纯属浪费;
    改为首次真实请求时惰性创建, 同一进程内每种取值只建一次。"""
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


_verify = False         # devices.json ngsoc.verify_ssl=true 时开启严格校验

_cfg = {}
_sites = []
EV_GAP_SECONDS = 12.5   # evidence接口限频5次/分钟/站点 -> 每次间隔12.5s

# 处置状态枚举(接口手册表3-1/5.1.6)
DISPOSE_STATES = {1: "待处置", 2: "处置中", 3: "已处置", 4: "不处置", 5: "误报"}

# ---- 按站点独立的evidence限频(并行安全: 锁内预约时隙, 锁外等待) ----
_EV_LOCK = threading.Lock()
_ev_slots = {}          # 站点名 -> 最近预约的调用时刻


def _reserve_evidence_slot(site_name):
    """预约该站点下一个evidence时隙。不同站点互不阻塞(限频按告警平台服务器独立),
    同站点的并行调用自动排队(时间片预约制)。"""
    now = time.time()
    with _EV_LOCK:
        earliest = max(now, _ev_slots.get(site_name, 0.0) + EV_GAP_SECONDS)
        _ev_slots[site_name] = earliest
    wait = earliest - now
    if wait > 0:
        time.sleep(wait)


def configure(cfg):
    global _cfg, _sites, _verify
    _cfg = cfg
    _verify = bool(cfg.get("ngsoc_verify_ssl"))
    if isinstance(cfg.get("ngsoc_sites"), list):
        # 内联站点(devices.json的ngsoc.sites, 设置界面卡片直配——url+key形态)
        _sites = [s for s in cfg["ngsoc_sites"] if isinstance(s, dict) and s.get("enabled", True)]
    elif cfg.get("ngsoc_sites_path"):
        # 外部站点文件(旧形态, 兼容保留): 无enabled字段, 全量生效
        with open(cfg["ngsoc_sites_path"], encoding="utf-8") as f:
            _sites = json.load(f).get("sites", [])
    else:
        _sites = []
    # 配置驱动注册(公开产品形态): 无站点配置时工具从模型视野移除——
    # 没部署该设备的公司不会看到这些工具, 也不会误调。
    from . import register_tool, unregister_tool
    if _sites:
        register_tool(SCHEMA_SEARCH, search_alarms, ["alert_search"])
        register_tool(SCHEMA_RAW, get_raw_alarms, ["evidence"])
        register_tool(SCHEMA_EVIDENCE, get_evidence, ["evidence"])
        register_tool(SCHEMA_DISPOSE, dispose_alarms, ["dispose", "write"])
    else:
        for n in ("ngsoc_search_alarms", "ngsoc_get_raw_alarms",
                  "ngsoc_get_evidence", "ngsoc_dispose_alarms"):
            unregister_tool(n)


def _site(name):
    n = (name or "").strip().lower()
    for s in _sites:
        if n in (s["name"], s["name"].lower(), (s.get("code") or "").lower(), s["url"].lower()):
            return s
    raise ValueError("未知站点 %r, 可用: %s" % (name, ", ".join(s["name"] for s in _sites)))


def _post(site, api, body, tries=3):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(
                site["url"] + api,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "告警平台-Access-Token": site["token"]},
            )
            with urllib.request.urlopen(req, context=_mk_ctx(_verify), timeout=90) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            time.sleep(3)
    raise last


def _get(site, api, tries=3):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(
                site["url"] + api,
                headers={"Content-Type": "application/json", "告警平台-Access-Token": site["token"]},
            )
            with urllib.request.urlopen(req, context=_mk_ctx(_verify), timeout=90) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            time.sleep(3)
    raise last


def _put(site, api, body, tries=2):
    """PUT写操作: HTTP错误不重试(避免误重复写), 直接返回错误详情"""
    import urllib.error
    try:
        req = urllib.request.Request(
            site["url"] + api, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "告警平台-Access-Token": site["token"]},
            method="PUT")
        with urllib.request.urlopen(req, context=_mk_ctx(_verify), timeout=90) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            body_txt = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body_txt = ""
        return {"http_error": e.code, "detail": body_txt}
    except Exception as e:
        if tries > 1:
            time.sleep(3)
            return _put(site, api, body, tries=1)
        return {"error": str(e)[:200]}


def _ts(v):
    try:
        if v:
            return datetime.datetime.fromtimestamp(int(v) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return v


def _ips(v):
    if isinstance(v, list):
        return ",".join(str(x) for x in v)
    return str(v) if v else "-"


# ---------------- packet_data 解码 ----------------

def _decode_packet(b64s):
    """base64报文 -> hexdump + 可打印字符串。分析基于全量长度, 展示头部1024字节。"""
    if not b64s:
        return None
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", str(b64s)))
    except Exception as e:
        return {"decode_error": str(e)[:120], "base64_head": str(b64s)[:200]}
    head = raw[:1024]
    lines = []
    for i in range(0, len(head), 16):
        chunk = head[i:i + 16]
        hexpart = " ".join("%02x" % b for b in chunk)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("%04x  %-47s  %s" % (i, hexpart, asciipart))
    strings = re.findall(rb"[\x20-\x7e]{4,}", raw[:4096])
    return {
        "bytes_total": len(raw),
        "shown_bytes": len(head),
        "hexdump": "\n".join(lines),
        "ascii_strings": [s.decode("ascii", "replace")[:120] for s in strings[:40]],
        "note": "bytes_total=IP头声明的捕获总长; shown<total时勿在截断数据上对畸形字段下定性(判据R-002)",
    }


# ---------------- 工具1: 告警检索 ----------------

SCHEMA_SEARCH = {
    "type": "function",
    "function": {
        "name": "ngsoc_search_alarms",
        "description": "检索告警平台告警(站点以devices.json的ngsoc.sites登记为准)。灵活入口: ①时间窗+站点批量拉取(如'A站今天10点以后的告警'→since参数) ②按源/目的IP拉行为链(建议72h) ③按告警名称过滤 ④按告警编号seq精确定位(用户只给编号时)。severity=严重度上限过滤(1致命-5提示, 值越小越严重; 如只要高危以上传2; 注意关联查询证据链时不要过滤严重度, Stratum握手是低危)。disposeState默认返回1待处置/2处置中/5误报(误报在列, 便于引用误报先例)。",
        "parameters": {
            "type": "object",
            "properties": {
                "site": {"type": "string", "description": "站点名(以devices.json ngsoc.sites登记为准)或'all'全站检索"},
                "src_ip": {"type": "string", "description": "按源IP过滤(主机行为链视角)"},
                "dst_ip": {"type": "string", "description": "按目的IP过滤"},
                "alarm_name": {"type": "string", "description": "按告警名称模糊过滤"},
                "alarm_seq": {"type": "number", "description": "按聚合告警编号seq精确定位(用户只给告警编号时用, 拉宽时间窗后自动过滤)"},
                "since": {"type": "string", "description": "从该时间点查到现在, 如'10:30'(今天)或'2026-11-04 10:30'或'昨天'"},
                "days": {"type": "number", "description": "回溯天数(与since二选一), 默认3; 行为链分析建议3-7"},
                "severity": {"type": "number", "description": "严重度上限: 只返回severity<=该值的(1致命/2高危/3中危/4低危/5提示)"},
                "size": {"type": "number", "description": "每站返回条数上限, 默认20"},
            },
            "required": ["site"],
        },
    },
}


def _compact_alarm(r):
    extra = r.get("extraFields")
    extra_s = ""
    if extra:
        try:
            extra_s = json.dumps(extra, ensure_ascii=False)[:400]
        except Exception:
            extra_s = str(extra)[:400]
    return {
        "seq": r.get("seq"),
        "name": r.get("name"),
        "severity": r.get("severity"),
        "times": r.get("times"),
        "first": _ts(r.get("timestamp")),
        "last": _ts(r.get("latestTimestamp")),
        "src": _ips(r.get("srcIp")),
        "dst": _ips(r.get("dstIp")),
        "attackResult": r.get("attackResult"),
        "commDirection": r.get("commDirection"),
        "uuid": r.get("uuid"),
        "timestamp": r.get("timestamp"),
        "latestTimestamp": r.get("latestTimestamp"),
        "relevantRuleId": r.get("relevantRuleId"),
        "relevantLogsType": r.get("relevantLogsType"),
        "triageReason": (str(r.get("triageReason"))[:200] if r.get("triageReason") else None),
        "extraFields_head": extra_s or None,
    }


def _since_ms(since):
    """时间点解析(与edr工具一致): '10:30'->今天10:30; '2026-11-04 10:30'->该时刻; '昨天'->昨天0点"""
    import datetime
    s = str(since).strip()
    if s in ("昨天", "昨日"):
        d = datetime.datetime.now() - datetime.timedelta(days=1)
        return int(d.replace(hour=0, minute=0, second=0).timestamp() * 1000)
    fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M", "%H:%M", "%H:%M:%S"]
    for f in fmts:
        try:
            d = datetime.datetime.strptime(s, f)
            now = datetime.datetime.now()
            if f in ("%H:%M", "%H:%M:%S"):
                d = now.replace(hour=d.hour, minute=d.minute, second=d.second, microsecond=0)
            elif "%Y" not in f:
                d = d.replace(year=now.year)
            return int(d.timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError("无法解析时间 %r, 示例: '10:30'(今天) / '2026-11-04 10:30' / '昨天'" % since)


def _search_one(site, src_ip, dst_ip, alarm_name, days, size, severity=None, alarm_seq=None, since=None):
    etime = int(time.time() * 1000)
    stime = etime - int(days * 86400 * 1000)
    if site.get("apiStyle") == "new":
        qp = {
            "maxSeq": -1,
            "assetsGroupView": 1,
            "domainId": "-1",
            "disposeState": "1,2,5",
            "timestamp": {"stime": stime, "etime": etime},
            "latestTimestamp": {"stime": stime, "etime": etime},
        }
        if src_ip:
            qp["srcIp"] = src_ip
        if dst_ip:
            qp["dstIp"] = dst_ip
        if alarm_name:
            qp["name"] = alarm_name
        resp = _post(site, "/api/v1/alarms/list-data",
                     {"view": "list", "queryParams": qp, "page": 1, "size": max(size, 20),
                      "orderBy": "-latestTimestamp"})
    else:
        qp = {
            "timestamp": {"stime": stime, "etime": etime},
            "latestTimestamp": {"stime": stime, "etime": etime},
        }
        if src_ip:
            qp["srcIp"] = src_ip
        if dst_ip:
            qp["dstIp"] = dst_ip
        if alarm_name:
            qp["name"] = alarm_name
        resp = _post(site, "/api/v1/alarms/list",
                     {"queryParams": qp, "page": 1, "size": 1000, "orderBy": "-latestTimestamp"})
    d = resp.get("data") or {}
    rows = d.get("data") or d.get("list") or []
    if alarm_name:
        rows = [r for r in rows if alarm_name in str(r.get("name", ""))]
    if severity is not None:
        try:
            rows = [r for r in rows if (r.get("severity") or 9) <= int(severity)]
        except Exception:
            pass
    if alarm_seq is not None:
        rows = [r for r in rows if str(r.get("seq")) == str(alarm_seq)]
    total = d.get("total") if isinstance(d, dict) else None   # API侧全量计数(过滤前)
    shown = rows[:size]
    out = {
        "total": total if isinstance(total, int) else len(rows),
        "matched": len(rows),       # 过滤后匹配数(seq/严重度过滤在应用层)
        "alarms": [_compact_alarm(r) for r in shown],
    }
    if isinstance(total, int) and total > len(shown):
        out["truncated"] = True
        out["note"] = ("API全量%d条, 本次返回%d条——批量分诊时概览应写'至少%d条'; 需更多可提高size或分时间窗"
                       % (total, len(shown), total))
    return out


def search_alarms(site, src_ip=None, dst_ip=None, alarm_name=None, days=3, size=20,
                  severity=None, alarm_seq=None, since=None):
    if since:
        stime = _since_ms(since)
        days = max((time.time() * 1000 - stime) / 86400000, 0.02)  # since优先, 折算成days
    if (site or "").strip().lower() in ("all", "全部", "六站"):
        # 全站并行(批量分诊高频操作; 站点间独立, 无共享限频——evidence限频才按站点计时)
        def _one(s):
            try:
                return s["name"], _search_one(s, src_ip, dst_ip, alarm_name, days, size,
                                              severity, alarm_seq)
            except Exception as e:
                return s["name"], {"error": str(e)[:200]}
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, max(1, len(_sites)))) as ex:
            for name, rows in ex.map(_one, _sites):
                results[name] = rows
        return {"mode": "all-sites", "params": {"src_ip": src_ip, "dst_ip": dst_ip,
                                                "name": alarm_name, "since": since,
                                                "days": round(days, 2)}, "sites": results}
    s = _site(site)
    out = _search_one(s, src_ip, dst_ip, alarm_name, days, size, severity, alarm_seq)
    return {"site": s["name"], "params": {"src_ip": src_ip, "dst_ip": dst_ip,
                                          "name": alarm_name, "since": since,
                                          "days": round(days, 2), "severity_max": severity,
                                          "seq": alarm_seq},
            "total": out.get("total"), "matched": out.get("matched"),
            "alarms": out.get("alarms", []),
            "truncated": out.get("truncated"), "note": out.get("note")}


# ---------------- 工具2: 原始告警 ----------------

SCHEMA_RAW = {
    "type": "function",
    "function": {
        "name": "ngsoc_get_raw_alarms",
        "description": "下钻某条聚合告警的原始告警列表(拿每条原始告警的uuid/时间/evidence类型, 是取报文证据的必经步骤)。入参全部来自ngsoc_search_alarms返回的告警字段。",
        "parameters": {
            "type": "object",
            "properties": {
                "site": {"type": "string", "description": "站点名"},
                "alarm_uuid": {"type": "string", "description": "聚合告警uuid"},
                "timestamp": {"type": "number", "description": "聚合告警首次时间戳(毫秒)"},
                "latest_timestamp": {"type": "number", "description": "聚合告警最新时间戳(毫秒)"},
                "limit": {"type": "number", "description": "返回条数上限, 默认8"},
            },
            "required": ["site", "alarm_uuid", "timestamp", "latest_timestamp"],
        },
    },
}


def get_raw_alarms(site, alarm_uuid, timestamp, latest_timestamp, limit=8):
    s = _site(site)
    resp = _get(s, "/api/v1/alarms/%s/raw-alarms?timestamp=%d&latestTimestamp=%d"
                % (urllib.parse.quote(str(alarm_uuid), safe=""),
                   int(timestamp), int(latest_timestamp)))
    d = resp.get("data") or {}
    groups = d.get("data") or []
    children = []
    for g in groups:
        children.extend(g.get("children") or [])
    children.sort(key=lambda c: c.get("timestamp") or 0)
    out = []
    for c in children[:limit]:
        out.append({
            "uuid": c.get("uuid"),
            "time": _ts(c.get("timestamp")),
            "timestamp": c.get("timestamp"),
            "relevantLogsType": c.get("relevantLogsType"),
            "victimIp": _ips(c.get("victimIp")),
            "attackerIp": _ips(c.get("attackerIp")),
            "attackResult": c.get("attackResult"),
            "hitNum": c.get("hitNum"),
        })
    return {"site": s["name"], "alarm_uuid": alarm_uuid, "raw_total": len(children),
            "raw_alarms": out,
            "note": ("仅对需报文定性的原始告警下钻ngsoc_get_evidence(限频12.5s/次)——"
                     "批量分诊时已知误报族引判据快速定性即可, 不要逐条下钻报文; "
                     "evidence入参用其uuid/timestamp/relevantLogsType")}


# ---------------- 工具3: 报文证据 ----------------

SCHEMA_EVIDENCE = {
    "type": "function",
    "function": {
        "name": "ngsoc_get_evidence",
        "description": "取某条原始告警的报文证据(evidence接口, 限频5次/分钟, 工具自动等待)。packet_data会自动base64解码为hexdump+可打印字符串。判据铁律: 注入/执行/WebShell/挖矿类未看报文不得下误报结论。",
        "parameters": {
            "type": "object",
            "properties": {
                "site": {"type": "string", "description": "站点名"},
                "alarm_uuid": {"type": "string", "description": "聚合告警uuid(必传)"},
                "raw_alarm_uuid": {"type": "string", "description": "原始告警uuid"},
                "raw_timestamp": {"type": "number", "description": "原始告警时间戳(毫秒)"},
                "rule_id": {"type": "string", "description": "聚合告警relevantRuleId"},
                "evidence_type": {"type": "string", "description": "日志类型, 取原始告警的relevantLogsType, 如flow_td_attack/flow_td_webattack"},
            },
            "required": ["site", "alarm_uuid", "raw_alarm_uuid", "raw_timestamp", "rule_id", "evidence_type"],
        },
    },
}

_KEEP_FIELDS = ["occur_time", "sip", "sport", "dip", "dport", "dev_rule_name", "dev_name",
               "action", "result", "result_code", "raw_attack_result", "uri", "uri_path",
               "http_method", "http_rsp_code", "attack_character", "x_forwarded_for",
               "xff", "host", "ioc", "hit_num", "attack_type_id", "pcap_id", "session_id",
               "geo_sip.country_name", "geo_dip.country_name"]
_BODY_FIELDS = ["http_req_header", "http_req_body", "http_rsp_header", "http_rsp_body"]


def get_evidence(site, alarm_uuid, raw_alarm_uuid, raw_timestamp, rule_id, evidence_type):
    s = _site(site)
    _reserve_evidence_slot(s["name"])   # 按站点限频(并行安全)
    q = urllib.parse.quote
    api = ("/api/v1/alarms/%s/evidence?type=%s&view=list&timestamp=%d&ruleId=%s&uuid=%s"
           % (q(str(raw_alarm_uuid), safe=""), q(str(evidence_type), safe=""),
              int(raw_timestamp), q(str(rule_id), safe=""), q(str(alarm_uuid), safe="")))
    resp = _get(s, api)
    h = resp.get("data") or {}
    h = h.get("data") if isinstance(h.get("data"), dict) else h
    hits = []
    if isinstance(h, dict):
        hits = ((h.get("search") or {}).get("hits")) or []
    out = []
    for hh in hits[:3]:
        sr = hh.get("_source") or {}
        row = {}
        for k in _KEEP_FIELDS:
            v = sr.get(k)
            if v not in (None, "", [], {}):
                row[k] = str(v)[:300] if k != "attack_character" else str(v)[:200]
        for k in _BODY_FIELDS:
            v = sr.get(k)
            if v not in (None, "", [], {}):
                row[k] = str(v)[:1600]
        pd = sr.get("packet_data")
        if pd:
            row["packet_decoded"] = _decode_packet(pd)
        out.append(row)
    return {"site": s["name"], "evidence_type": evidence_type, "hits": len(hits), "records": out,
            "note": ("0 hits时可尝试换evidence_type: flow_td_attack/flow_td_webattack/flow_td_ioc; "
                     "packet_data为空时需用户从告警平台告警详情页取'上行/下行前N字节'样本(判据R-003); "
                     "注意先看包方向(sport/dport判断请求还是响应)再解读")}


# ---------------- 工具4: 处置回写(可执行模式专用) ----------------

SCHEMA_DISPOSE = {
    "type": "function",
    "function": {
        "name": "ngsoc_dispose_alarms",
        "description": "[仅可执行模式] 批量处置告警回写(PUT /api/v1/alarms/dispose-state)。state: 3已处置/4不处置/5误报。执行纪律: ①必须先向用户复述告警清单(seq/名称/uuid/定性/理由)并获用户明确同意 ②用户同意后带confirmed=true调用 ③执行时系统会弹出权限确认窗, 用户点'允许'才真正回写 ④误报定性必须有报文级证据支撑(判据R-001)。endTime=该聚合告警的latestTimestamp(毫秒)。",
        "parameters": {
            "type": "object",
            "properties": {
                "site": {"type": "string", "description": "站点名"},
                "alarm_uuid": {"type": "string", "description": "聚合告警uuid(单条)"},
                "state": {"type": "number", "description": "处置状态: 3=已处置, 4=不处置, 5=误报"},
                "end_time": {"type": "number", "description": "该告警latestTimestamp(毫秒)"},
                "start_time": {"type": "number", "description": "该告警timestamp(毫秒, 首次时间)"},
                "desc": {"type": "string", "description": "处置描述(定性结论+依据摘要)"},
                "confirmed": {"type": "boolean", "description": "用户已明确确认执行=true; 否则会被拒绝"},
            },
            "required": ["site", "alarm_uuid", "state", "end_time", "confirmed"],
        },
    },
}


def dispose_alarms(site, alarm_uuid, state, end_time, confirmed=False, desc="", start_time=None):
    if not confirmed:
        return {"refused": True,
                "reason": "未获用户确认。请先向用户复述要处置的告警清单(uuid/名称/seq/定性/理由), 用户明确同意后再带confirmed=true执行"}
    # ---- 权限门: 处置回写是写操作, 必须经人工确认弹窗(ask)。
    # LLM自报的confirmed只是纪律提示, 不能替代人工确认——防线在permissions引擎。
    state_names = {3: "已处置", 4: "不处置", 5: "误报"}
    detail = ("告警平台告警处置回写\n站点: %s\n告警uuid: %s\n状态: %s\n说明: %s"
              % (site, alarm_uuid, state_names.get(state, str(state)),
                 (desc or "")[:300]))
    reason, ok = _perm.gate("dispose", "%s|%s|state=%s" % (site, alarm_uuid, state), detail)
    if not ok:
        return {"refused": True, "reason": reason}
    try:
        state = int(state)
    except Exception:
        return {"error": "state必须为数字"}
    if state not in (3, 4, 5):
        return {"error": "state仅支持 3已处置/4不处置/5误报"}
    s = _site(site)
    body = {
        "alarmIds": str(alarm_uuid),
        "state": state,
        "endTime": int(end_time),
        "desc": desc or "",
    }
    if start_time:
        body["startTime"] = int(start_time)
    resp = _put(s, "/api/v1/alarms/dispose-state", body)
    if isinstance(resp, dict) and resp.get("errCode") == 0:
        return {"success": True, "alarm_uuid": alarm_uuid,
                "state": "%d(%s)" % (state, DISPOSE_STATES.get(state)),
                "site": s["name"], "desc": desc,
                "note": "处置回写成功; 此操作已记录在会话日志, 可作为研判闭环证据"}
    return {"result": resp,
            "note": "返回errCode非0或http_error时: 检查end_time是否为该告警latestTimestamp、uuid是否正确; 如404则接口路径需按版本调整"}
