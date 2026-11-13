# -*- coding: utf-8 -*-
"""主机防护平台主机侧告警查询工具(依赖D盘现有edr_client.py, 运行时sys.path加载)
支持灵活入口: 时间点(since)/项目/告警ID/主机名; 状态过滤(PENDING未处理)
"""
import sys, time, json, datetime, threading

_cfg = {}
_CLIENTS = {}
_CLIENTS_LOCK = threading.Lock()
_PROJECTS = {}   # 项目名 -> (server_url, token), 来自devices.json的edr.projects

PROJECT_SITE_MAP = "项目与站点映射按你的环境在devices.json中登记; 检测详情含进程树/文件哈希/完整cmd"
SEV_ALL = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']


def configure(cfg):
    global _cfg, _PROJECTS
    _cfg = cfg
    _PROJECTS = dict(cfg.get("edr_projects") or {})
    p = cfg.get("edr_client_path")
    if p and p not in sys.path:
        sys.path.append(p)   # append而非insert(0): 避免外部目录遮蔽本project模块
    # 配置驱动注册(公开产品形态): 未登记任何项目时, 主机侧查询工具从模型视野移除。
    from . import register_tool, unregister_tool
    if _PROJECTS:
        register_tool(SCHEMA, query, ["host", "alert_search"])
    else:
        unregister_tool("edr_query")


def parse_since(since):
    """时间点解析: '10:30'->今天10:30; '2026-11-04 10:30'/'11-04 10:30'->该时刻; '昨天'->昨天0点"""
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
            else:
                if "%Y" not in f:
                    d = d.replace(year=now.year)
            return int(d.timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError("无法解析时间 %r, 示例: '10:30'(今天) / '2026-11-04 10:30' / '昨天'" % since)


def _client(project):
    project = project.replace('&极光云', '')
    if project not in _PROJECTS:
        raise ValueError("未知项目 %r, 可用: %s (在devices.json的edr.projects中登记: name+server+token)"
                         % (project, ",".join(_PROJECTS) or "未登记任何项目"))
    with _CLIENTS_LOCK:
        if project not in _CLIENTS:
            from edr_client import EdrClient
            url, token = _PROJECTS[project]
            _CLIENTS[project] = EdrClient(url, token, timeout=30)
        return _CLIENTS[project]


def _fmt_time(utc_str):
    try:
        from datetime import timedelta
        dt = datetime.datetime.fromisoformat(utc_str.replace('Z', '+00:00'))
        return (dt + timedelta(hours=8)).strftime('%m-%d %H:%M:%S') + "(北京时间)"
    except Exception:
        return utc_str


_SEV_ORDER = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}
_MAX_RETURN = 200      # list/host 返回条数上限(再多会撑爆上下文, 模型按total决定是否分批)


def _row(d):
    b = d['base_info']
    code = b.get('detection_code', '')
    hid = code.replace('QT-D', '') if code.startswith('QT-D') else code
    return {"time": _fmt_time(b.get('detection_time', '?')), "severity": b.get('severity'),
            "status": b.get('status'), "title": b.get('detection_title'), "id": hid}


def _sort_and_cap(alarms):
    """批量分诊就绪: 按严重度排序(危急/高危优先), 超200条截断并明确标注"""
    alarms.sort(key=lambda d: _SEV_ORDER.get(
        str((d.get('base_info') or {}).get('severity') or '').upper(), 9))
    out = {"total": len(alarms), "alarms": [_row(d) for d in alarms[:_MAX_RETURN]]}
    if len(alarms) > _MAX_RETURN:
        out["truncated"] = True
        out["note"] = ("命中%d条, 已按严重度排序返回前%d条(危急/高危优先)——"
                       "概览应写'至少%d条'; 中低危剩余量可分批/分时间窗处理"
                       % (len(alarms), _MAX_RETURN, len(alarms)))
    return out


def _shrink(obj, max_str=4000):
    """JSON感知瘦身: 超长字符串字段截断, 保持结构合法(比硬切dump保真)"""
    if isinstance(obj, dict):
        return {k: _shrink(v, max_str) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shrink(v, max_str) for v in obj]
    if isinstance(obj, str) and len(obj) > max_str:
        return obj[:max_str] + " …[字段超长已截断]"
    return obj


SCHEMA = {
    "type": "function",
    "function": {
        "name": "edr_query",
        "description": "查主机防护平台主机侧告警(主机侧证据权威于流量推断)。灵活入口: ①'XX时间以后XX项目未处理的告警'→action=list+project+since+status=PENDING ②告警ID(QT-D-开头的24位hex)→action=detail+detection_id+project ③主机名→action=host。项目名以devices.json的edr.projects登记为准。status: PENDING=未处理(运营中最常用)/TRUE_POSITIVE已确认/FALSE_POSITIVE已忽略(注意可能被误标需复核)。时间用since('10:30'今天该时刻/'2026-11-04 10:30'/'昨天')或hours(回溯N小时)二选一。detection_time是UTC已自动+8。主机侧不记录植入进程网络连接。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["host", "list", "detail"],
                           "description": "list=项目告警列表(时间窗批量研判用) / host=按主机名查告警史 / detail=单条告警完整证据(进程树/哈希/完整cmd)"},
                "project": {"type": "string", "description": "主机侧项目名(以devices.json的edr.projects登记为准)"},
                "hostname": {"type": "string", "description": "action=host时: 主机名(模糊匹配)"},
                "detection_id": {"type": "string", "description": "action=detail时: 告警ID(24位hex, QT-D-前缀会自动去掉)"},
                "since": {"type": "string", "description": "从该时间点查到现在, 如'10:30'(今天)或'2026-11-04 10:30'或'昨天'"},
                "hours": {"type": "number", "description": "回溯小时数(与since二选一); host默认72/list默认24"},
                "status": {"type": "string", "enum": ["PENDING", "TRUE_POSITIVE", "FALSE_POSITIVE"],
                           "description": "只返回该状态的告警, 如用户说'未处理'则传PENDING; 不传返回全部"},
            },
            "required": ["action", "project"],
        },
    },
}


def query(action, project, hostname=None, detection_id=None, hours=None, since=None, status=None):
    c = _client(project)
    now_ms = int(time.time() * 1000)
    if since:
        start_ms = parse_since(since)
    else:
        h = hours or (72 if action == "host" else 24)
        start_ms = now_ms - h * 3600000
    if action == "detail":
        if not detection_id:
            raise ValueError("action=detail需要detection_id")
        d = c.query_detection_detail(detection_id.replace('QT-D', ''))
        if not d:
            return {"error": "未查到(同服务器多项目时token不同, 试换项目重查)"}
        # JSON感知瘦身: 超长字段截断保持结构合法, 再dump兜底——硬切dump会产生畸形JSON
        s = json.dumps(_shrink(d, 4000), ensure_ascii=False, indent=1)
        truncated = len(s) > 16000
        out = {"detail": s[:16000],
               "note": "detection_msg含进程树/ppname/文件哈希/完整cmd; 文件哈希应送情报查询"}
        if truncated:
            out["detail_truncated"] = True
            out["note"] += "; 详情超长已按字段截断, 关键结论字段(detection_msg/process树)优先看"
        return out
    if action == "host":
        items = c.query_detections(SEV_ALL, start_ms=start_ms, end_ms=now_ms,
                                   page_size=100, max_pages=16)
        hits = [d for d in items if (hostname or '').lower() in d['base_info'].get('hostname', '').lower()]
        if status:
            hits = [d for d in hits if d['base_info'].get('status') == status]
        out = _sort_and_cap(hits)
        out.update({"project": project, "hostname": hostname,
                    "window": "since %s" % datetime.datetime.fromtimestamp(start_ms / 1000).strftime("%m-%d %H:%M")
                              if since else "近%sh" % (hours or 72),
                    "status_filter": status})
        return out
    if action == "list":
        # 分页穷尽上限16页(1600条), 拉满时明确告知"可能未拉全"
        items = c.query_detections(SEV_ALL, start_ms=start_ms, end_ms=now_ms,
                                   page_size=100, max_pages=16)
        may_more = len(items) >= 1600
        if status:
            items = [d for d in items if d['base_info'].get('status') == status]
        out = _sort_and_cap(items)
        out.update({"project": project,
                    "window": "since %s" % datetime.datetime.fromtimestamp(start_ms / 1000).strftime("%m-%d %H:%M")
                              if since else "近%sh" % (hours or 24),
                    "status_filter": status,
                    "note": (out.get("note") or "") + ("; " if out.get("note") else "")
                            + "批量研判时: 已知误报族引用判据快速定性, 只对需主机侧证据的可疑告警下钻detail; 文件哈希应送情报查询"})
        if may_more:
            out["may_be_truncated"] = True
            out["note"] += "; ⚠命中已达单次拉取上限1600条, 时间窗内可能还有更多——概览须写'至少', 必要时缩小时间窗分批"
        return out
    raise ValueError("未知action %r" % action)
