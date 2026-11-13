# -*- coding: utf-8 -*-
"""统一安全能力入口 —— 模型只面向这三个工具, 不再面向各家设备的专用参数。

  sec_probe   设备自检: 逐设备探测鉴权/可达性/可拉到的告警条数与字段, 输出能力矩阵
  sec_search  项目内聚合检索: 并发查该项目全部启用设备 → 归一化 → 按指纹聚类(同告警合并)
  sec_detail  统一下钻: 按告警ID路由到对应适配器取报文/证据

设计意图: 研判方法论(system.md)与能力标签(caps.py)保持设备无关; 换设备只需加适配器,
提示词与流程零改动。旧 ngsoc_*/edr_query 工具保留, 作为设备特有深度能力(报文解码/处置回写)。
"""
import concurrent.futures
import datetime

from .adapters import build_adapter, build_projects, merge_alarms

_state = {"projects": [], "registered": False}
_ref_cache = {}          # alarm_id -> raw_ref (最近一次检索结果, 供 sec_detail 直接下钻)


def _now_ms():
    return int(datetime.datetime.now().timestamp() * 1000)


def _parse_since(since, hours):
    """since 支持 '2h' / '今天' / '2026-11-10 10:00'; 解析失败回退 hours。"""
    if since:
        try:
            from .edr import parse_since
            return int(parse_since(since))
        except Exception:
            pass
    return _now_ms() - int(float(hours or 24) * 3600000)


def _devices(project=None):
    out = []
    for p in _state["projects"]:
        if project and p["name"] != project:
            continue
        for d in p.get("devices") or []:
            if d.get("enabled", True) and str(d.get("kind") or "").strip():
                out.append((p["name"], d))
    return out


def _project_names():
    names = [p["name"] for p in _state["projects"]]
    return names or ["(未配置设备)"]


# ---------------- 工具1: 设备自检 ----------------

SCHEMA_PROBE = {
    "type": "function",
    "function": {
        "name": "sec_probe",
        "description": "安全设备自检(只读): 逐个设备校验 token 权限与可达性, 返回每台设备能拉到多少条告警、"
                       "哪些字段可用、失败原因。研判前若不确定某设备是否可用/权限够不够, 先调这个, "
                       "不要凭猜测调用检索。project 不传则检查全部项目。",
        "parameters": {"type": "object", "properties": {
            "project": {"type": "string", "description": "项目名(不传=全部项目)"},
        }},
    },
}


def sec_probe(project=None, **_ignored):
    pairs = _devices(project)
    if not pairs:
        return {"error": "没有已配置且启用的设备", "projects": _project_names(),
                "hint": "在设置中心「系统API接入 → 项目与设备组」添加设备"}
    results, groups = [], {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(build_adapter(d, p).probe): (p, d) for p, d in pairs}
        for fut in concurrent.futures.as_completed(futs):
            p, d = futs[fut]
            try:
                row = fut.result()
            except Exception as e:
                row = {"device": d.get("name"), "kind": d.get("kind"), "ok": False,
                       "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
            results.append(row)
            groups.setdefault(p, []).append(row)
    return {"projects": [{"name": p, "devices": rows} for p, rows in groups.items()],
            "summary": "可用 %d / %d 台" % (sum(1 for r in results if r.get("ok")), len(results))}


# ---------------- 工具2: 聚合检索 ----------------

SCHEMA_SEARCH = {
    "type": "function",
    "function": {
        "name": "sec_search",
        "description": "在项目内聚合检索告警(只读): 并发查该项目下全部启用设备, 字段归一化后按指纹聚类——"
                       "同一条攻击被流量侧/WAF/主机侧同时命中会合并为一条并标注 sources。返回统一告警对象"
                       "(time/name/severity_label/src_ip/dst_ip/host/count/sources)。批量分诊优先用这个, "
                       "不要逐设备循环调用。",
        "parameters": {"type": "object", "properties": {
            "project": {"type": "string", "description": "项目名(设备组); 不传=全部项目"},
            "hours": {"type": "number", "description": "回溯小时数(默认24)"},
            "since": {"type": "string", "description": "起始时间, 如'今天'/'2026-11-10 10:00'/'2h'(与hours二选一)"},
            "src_ip": {"type": "string", "description": "源IP过滤(可选)"},
            "dst_ip": {"type": "string", "description": "目的IP过滤(可选)"},
            "alarm_name": {"type": "string", "description": "告警名称关键字(可选, 客户端模糊匹配)"},
            "severity": {"type": "string", "description": "严重度过滤(可选, 设备支持时下推)"},
            "limit": {"type": "number", "description": "每台设备最多返回条数(默认30, 上限100)"},
        }},
    },
}


def sec_search(project=None, hours=24, since=None, src_ip=None, dst_ip=None,
               alarm_name=None, severity=None, limit=30, **_ignored):
    pairs = _devices(project)
    if not pairs:
        return {"error": "没有已配置且启用的设备", "projects": _project_names()}
    limit = min(int(limit or 30), 100)
    since_ms = _parse_since(since, hours)
    query = {"since_ms": since_ms, "until_ms": _now_ms(), "limit": limit,
             "src_ip": src_ip, "dst_ip": dst_ip, "alarm_name": alarm_name,
             "severity": severity, "hours": hours,
             "days": max((_now_ms() - since_ms) / 86400000.0, 0.02)}

    rows, per_device = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(build_adapter(d, p).search, query): (p, d) for p, d in pairs}
        for fut in concurrent.futures.as_completed(futs):
            p, d = futs[fut]
            try:
                got = fut.result() or []
                rows.extend(got)
                per_device.append({"device": d.get("name"), "kind": d.get("kind"),
                                   "ok": True, "n": len(got)})
            except Exception as e:
                per_device.append({"device": d.get("name"), "kind": d.get("kind"),
                                   "ok": False, "n": 0,
                                   "error": "%s: %s" % (type(e).__name__, str(e)[:160])})

    filtered_empty = False
    if alarm_name:
        key = str(alarm_name).lower()
        kept = [r for r in rows if key in str(r.get("name") or "").lower()]
        if rows and not kept:
            filtered_empty = True
        rows = kept

    for r in rows:
        if r.get("alarm_id") and r.get("raw_ref"):
            _ref_cache[str(r["alarm_id"])] = r["raw_ref"]

    merged = merge_alarms(rows)
    clusters = [m for m in merged if m.get("merged", 1) > 1]
    out = {
        "project": project or "全部项目",
        "window": "近%s小时" % hours if not since else str(since),
        "devices": per_device,
        "total": len(rows),
        "merged_total": len(merged),
        "multi_source": len(clusters),
        "alarms": merged[:max(limit * max(1, len(per_device)), limit)],
    }
    if filtered_empty:
        out["note"] = "没有告警名匹配 %r, 已返回空结果; 去掉 alarm_name 可看全部" % alarm_name
    elif clusters:
        out["note"] = ("有 %d 条告警被多台设备同时命中, 已合并并在 sources 标注来源; "
                       "这类组合证据优先用于定性" % len(clusters))
    return out


# ---------------- 工具3: 统一下钻 ----------------

SCHEMA_DETAIL = {
    "type": "function",
    "function": {
        "name": "sec_detail",
        "description": "统一下钻单条告警的证据(只读): 给告警ID, 自动路由到该设备适配器取原始告警/报文。"
                       "告警ID 用 sec_search 返回的 alarm_id; 若不确定设备可只传 project+alarm_id, "
                       "系统会在该项目内匹配来源设备。",
        "parameters": {"type": "object", "properties": {
            "alarm_id": {"type": "string", "description": "sec_search 返回的 alarm_id"},
            "project": {"type": "string", "description": "项目名(可选, 缩小范围)"},
            "device": {"type": "string", "description": "设备名(可选, 已知来源时指定)"},
        }, "required": ["alarm_id"]},
    },
}


def sec_detail(alarm_id, project=None, device=None, **_ignored):
    ref = _ref_cache.get(str(alarm_id))
    if not ref:
        return {"error": "未找到该告警的下钻凭据", "alarm_id": alarm_id,
                "hint": "先用 sec_search 检索一次(会缓存凭据), 再用同一个 alarm_id 下钻"}
    pairs = _devices(project)
    target = None
    for p, d in pairs:
        if device and d.get("name") != device:
            continue
        if ref.get("kind") and str(d.get("kind")) == str(ref.get("kind")):
            target = (p, d)
            break
    if not target and pairs:
        target = pairs[0]
    if not target:
        return {"error": "找不到对应设备", "device": device, "project": project}
    p, d = target
    try:
        return {"device": d.get("name"), "project": p, "alarm_id": alarm_id,
                "detail": build_adapter(d, p).detail(ref)}
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:200]),
                "device": d.get("name"), "alarm_id": alarm_id}


# ---------------- 注册 ----------------

def configure(cfg):
    from . import register_tool, unregister_tool
    import caps as _caps
    for n in ("sec_probe", "sec_search", "sec_detail"):
        unregister_tool(n)
    _state["projects"] = build_projects(cfg)
    if not _state["projects"]:
        _state["registered"] = False
        return
    # unregister 会摘掉能力标签, 注册时必须从静态表回填, 否则提示词能力清单缺项
    for schema, handler in ((SCHEMA_PROBE, sec_probe), (SCHEMA_SEARCH, sec_search),
                            (SCHEMA_DETAIL, sec_detail)):
        name = schema["function"]["name"]
        register_tool(schema, handler, capabilities=_caps.STATIC_CAPABILITIES.get(name))
    _state["registered"] = True


def projects():
    return _state["projects"]
