# -*- coding: utf-8 -*-
"""威胁情报查询(多源自动降级, 不死板):
  商业情报v3 API(key已配置时) -> ThreatFox(abuse.ch, 免费无key) -> ip-api归属地/IDC/代理标记
  三源并行查询(单源最坏60s超时, 串行会拖慢研判; 并行后耗时≈最慢一源)。
内网IP自动跳过(走资产库/主机侧)。全部失败才提示人工。禁止用记忆替代情报。
"""
import json, ipaddress, concurrent.futures

_cfg = {}


def configure(cfg):
    global _cfg
    _cfg = cfg


def _is_private(ip):
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback or a.is_reserved
    except ValueError:
        return False


def _threatbook(ip, key):
    import requests
    r = requests.post("https://api.threatbook.cn/v3/scene/ip_report",
                      params={"apikey": key}, data={"resource": ip}, timeout=60)
    j = r.json()
    data = j.get("data") or {}
    s = json.dumps({"severity_level": data.get("severity_level"), "tags": data.get("tags"),
                    "judgments": data.get("judgments"), "basic": data.get("basic")},
                   ensure_ascii=False)
    return json.loads(s) if len(s) <= 8000 else {"severity_level": data.get("severity_level"),
                                                 "tags": data.get("tags"), "raw_trimmed": s[:8000]}


def _threatfox(ip):
    """ThreatFox (abuse.ch) IOC检索——免费无key"""
    import requests
    r = requests.post("https://threatfox-api.abuse.ch/api/v1/",
                      json={"query": "search_ioc", "search_term": ip}, timeout=25)
    j = r.json()
    data = j.get("data")
    if not data or data == "no-result":
        return {"found": False, "note": "ThreatFox无记录(无记录≠安全)"}
    rows = data if isinstance(data, list) else []
    return {"found": True, "records": [
        {"malware": d.get("malware_printable"), "confidence_level": d.get("confidence"),
         "first_seen": d.get("first_seen"), "last_seen": d.get("last_seen"),
         "malware_alias": d.get("malware_alias")}
        for d in rows[:5]]}


def _ipgeo(ip):
    """归属地+ISP查询——HTTPS免key源优先(公司网络拦HTTP): ipwho.is -> ipapi.co -> ip-api.com"""
    import requests
    # 1) ipwho.is (HTTPS)
    try:
        j = requests.get("https://ipwho.is/" + ip, timeout=15).json()
        if j.get("success") and j.get("country"):
            conn = j.get("connection") or {}
            return {"source": "ipwho.is", "country": j.get("country"),
                    "country_code": j.get("country_code"), "region": j.get("region"),
                    "city": j.get("city"), "isp": conn.get("isp"), "org": conn.get("org"),
                    "asn": conn.get("asn"),
                    "note": "country用于判据R-009(国外IP规律爆破快速定性)的国外/国内判断; ISP为机房/托管商时提示服务器属性"}
    except Exception:
        pass
    # 2) ipapi.co (HTTPS)
    try:
        j = requests.get("https://ipapi.co/%s/json/" % ip, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"}).json()
        if j.get("country_name"):
            return {"source": "ipapi.co", "country": j.get("country_name"),
                    "region": j.get("region"), "city": j.get("city"), "isp": j.get("org"),
                    "org": j.get("org"), "asn": j.get("asn"),
                    "note": "country用于判据R-009的国外/国内判断"}
    except Exception:
        pass
    # 3) ip-api.com (HTTP, 部分网络拦截80端口, 兜底)
    try:
        j = requests.get("http://ip-api.com/json/%s?fields=status,country,countryCode,regionName,"
                         "city,isp,org,as,proxy,hosting&lang=zh-CN" % ip, timeout=12).json()
        if j.get("status") == "success":
            return {"source": "ip-api.com", "country": j.get("country"),
                    "region": j.get("regionName"), "city": j.get("city"), "isp": j.get("isp"),
                    "org": j.get("org"), "proxy": j.get("proxy"), "hosting_idc": j.get("hosting"),
                    "note": "proxy/hosting=代理或IDC机房标记"}
    except Exception:
        pass
    return {"error": "三个归属地源均不可达(网络限制), 请人工查询归属"}


SCHEMA = {
    "type": "function",
    "function": {
        "name": "threatbook_ip",
        "description": "查询外网IP威胁情报(多源自动查询: 商业情报key已配置时用商业情报v3; 无论有无key都会查ThreatFox恶意IOC库+ip-api归属地/IDC/代理标记)。内网IP自动跳过。铁律: 外网攻击源IP定性前必须查询(行为+情报双确认才可建议封禁); 禁止用训练记忆代替情报(不得凭记忆说'巴西段/南美IP'); 无记录≠安全。",
        "parameters": {
            "type": "object",
            "properties": {"ip": {"type": "string", "description": "要查询的IP"}},
            "required": ["ip"],
        },
    },
}


def threatbook_ip(ip):
    if _is_private(ip):
        return {"ip": ip, "skip": True,
                "note": "内网IP不适用外网情报查询——请走netbox_lookup资产归属+主机侧主机侧排查"}
    out = {"ip": ip, "sources": {},
           "rule": "封禁类处置必须情报+行为双确认(判据R-008); 无记录≠安全; 禁止用记忆替代"}
    key = (_cfg.get("threatbook_api_key") or "").strip()
    if not key:
        out["sources"]["threatbook(商业情报)"] = {
            "status": "skip",
            "reason": "未配置key, 已自动降级下方情报源; 配置后可获得权威标签"}
    # 三源并行(商业情报60s/ThreatFox 25s/归属地42s——串行最坏127s, 并行≈最慢一源)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {}
        if key:
            futs["threatbook(商业情报)"] = ex.submit(_threatbook, ip, key)
        futs["threatfox"] = ex.submit(_threatfox, ip)
        futs["ip_geo"] = ex.submit(_ipgeo, ip)
        for name, fut in futs.items():
            try:
                out["sources"][name] = fut.result()
            except Exception as e:
                out["sources"][name] = {"error": str(e)[:150]}
    out["manual_url"] = "https://x.threatbook.cn/ip/" + ip
    out["note"] = ("多源结果供交叉判断; 需要权威标签(僵尸网络/扫描器家族等)时可人工复核manual_url。"
                   "各源失败时如实报告, 不得因源不可用而跳过情报环节或用记忆填空。")
    return out
