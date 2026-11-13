# -*- coding: utf-8 -*-
"""资产库 IP归属查询工具(判据: 告警中所有IP必查, 含公网; 三级回退 q->32->24)"""
import json, ssl, time, urllib.request, urllib.parse, re
import functools


@functools.lru_cache(maxsize=2)
def _mk_ctx(verify):
    """惰性+缓存, 避免启动期枚举系统根证书(约 55ms/次)。"""
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


_verify = False   # devices.json netbox.verify_ssl=true 时开启严格校验

_cfg = {}


def configure(cfg):
    global _cfg, _verify
    _cfg = cfg
    _verify = bool(cfg.get("netbox_verify_ssl"))
    # 配置驱动注册(公开产品形态): 无资产库配置时, 资产归属查询工具从模型视野移除。
    from . import register_tool, unregister_tool
    if _cfg.get("netbox_base"):
        register_tool(SCHEMA, lookup_ip, ["asset"])
    else:
        unregister_tool("netbox_lookup")


def _fetch(url):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + _cfg["netbox_token"]})
    last = None
    for _ in range(2):          # 瞬时抖动重试1次(与ngsoc/edr对齐)
        try:
            with urllib.request.urlopen(req, context=_mk_ctx(_verify), timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(0.5)
    raise last


def _lookup(ip):
    base = _cfg["netbox_base"]
    ip = urllib.parse.quote(str(ip), safe="")
    strategies = [
        "?q=%s&limit=50" % ip,
        "?address=" + urllib.parse.quote(str(ip) + "/32"),
        "?address=" + urllib.parse.quote(str(ip) + "/24"),
    ]
    for s in strategies:
        try:
            data = _fetch(base + s)
            for rec in data.get("results", []):
                if rec["address"].split("/")[0] == ip:
                    return rec
        except Exception:
            continue
    return None


SCHEMA = {
    "type": "function",
    "function": {
        "name": "netbox_lookup",
        "description": "查询IP的资产库资产归属(平台/业务系统/责任人/IP类型/项目)。研判时告警中所有IP(内网+公网)都必须查, 禁止凭假设跳过。**批量分诊**: 多个IP可用逗号/分号分隔一次查询(≤20个), 显著减少调用次数。ROUTER_GW/ROUTER_INT=虚拟路由器网关(NAT后视图, 真实源在网关后); FLOATING=浮动IP; VM_FIXED_IP=虚机固定IP。多VRF环境按站点选VRF(映射关系记入知识库E类条目)。资产库无记录≠不存在(管理网段/容器网段可能不在IPAM)。",
        "parameters": {
            "type": "object",
            "properties": {"ip": {"type": "string",
                                  "description": "IP地址; 批量时逗号/分号/空格分隔多个(≤20)"}},
            "required": ["ip"],
        },
    },
}


def _fmt_record(rec, ip):
    cf = rec.get("custom_fields") or {}
    desc = rec.get("description") or ""
    proj = re.search(r"proj=(\S+)", desc)
    return {
        "ip": ip,
        "found": True,
        "platform_vrf": (rec.get("vrf") or {}).get("display", "-"),
        "business": ",".join(cf.get("BusinessSystem") or ["-"]),
        "owner": cf.get("owner", "-"),
        "owner_email": cf.get("owner_email", "-"),
        "ip_role": cf.get("ip_role", "-"),
        "status": (rec.get("status") or {}).get("value", "-"),
        "project": proj.group(1) if proj else None,
        "description": desc[:200],
    }


def lookup_ip(ip):
    ips = [x for x in re.split(r"[;,，\s]+", str(ip or "").strip()) if x]
    if not ips:
        return {"error": "ip为空"}
    if len(ips) > 1:   # 批量(分诊场景一次查完整告警的源目IP集)
        if len(ips) > 20:
            return {"error": "单次最多20个IP, 请分批"}
        results = {}
        for x in ips[:20]:
            try:
                rec = _lookup(x)
                results[x] = _fmt_record(rec, x) if rec else \
                    {"ip": x, "found": False,
                     "note": "资产库无记录≠不存在: 管理网段、容器/pod网段可能不在IPAM; 外网IP需用户确认归属"}
            except Exception as e:
                results[x] = {"ip": x, "error": str(e)[:120]}
        return {"batch": True, "count": len(results), "results": results}
    rec = _lookup(ips[0])
    if not rec:
        return {"ip": ips[0], "found": False,
                "note": "资产库无记录≠不存在: 管理网段、容器/pod网段可能不在IPAM; 外网IP需用户确认归属"}
    return _fmt_record(rec, ips[0])
