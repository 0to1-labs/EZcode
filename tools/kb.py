# -*- coding: utf-8 -*-
"""判据知识库检索: 判据/产品缺陷/报文指纹/先例案例/环境情报"""
import json, os, re
from . import permissions as _perm

_KB = []
_BASE = os.path.dirname(os.path.abspath(__file__))


def configure(cfg):
    global _KB
    path = os.path.join(_BASE, "..", "knowledge", "criteria.json")
    with open(path, encoding="utf-8") as f:
        _KB = json.load(f).get("entries", [])


SCHEMA = {
    "type": "function",
    "function": {
        "name": "kb_search",
        "description": "检索研判知识库(判据/告警平台产品缺陷/报文指纹/历史先例/环境情报)。研判任何告警前先查: 用告警名称+关键特征做关键词(如'SQL注入 同形误报'/'STRATUM 矿池'/'XFF'/'NAT 网关')。匹配到的判据必须在研判结论中引用其编号。**批量分诊**: 多条告警的判据对照可用分号分隔多组关键词一次查询(如'挖矿误报; SSH爆破; WebShell', ≤10组), 避免逐条调用烧工具轮次。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词, 空格/逗号分隔; 批量时用分号或换行分隔多组(每组对应一条告警的特征)"},
                "limit": {"type": "number", "description": "每组返回条数上限, 默认5"},
            },
            "required": ["query"],
        },
    },
}


SCHEMA_ADD = {
    "type": "function",
    "function": {
        "name": "kb_add_entry",
        "description": "[仅可执行模式] 沉淀新判据/先例/指纹到知识库criteria.json(终身学习闭环, 技能库)。id前缀: R=判据 D=产品缺陷 F=报文指纹 C=先例 E=环境。执行纪律: 必须先向用户复述完整条目内容并获用户同意(confirmed=true); 执行时系统会弹权限确认窗, 用户点'允许'才真正写入。",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "条目编号, 如R-015/C-011, 前缀字母-三位数字"},
                "category": {"type": "string", "enum": ["rule", "defect", "fingerprint", "case", "env"],
                             "description": "rule=判据 defect=产品缺陷 fingerprint=报文指纹 case=先例 env=环境"},
                "title": {"type": "string", "description": "条目标题(30字内)"},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "检索关键词列表(kb_search按此命中)"},
                "content": {"type": "string", "description": "条目正文: 现象/判据内容/先例事实/适用边界"},
                "confirmed": {"type": "boolean", "description": "用户已明确同意写入=true; 否则被拒绝"},
            },
            "required": ["id", "category", "title", "tags", "content", "confirmed"],
        },
    },
}

_ID_PAT = re.compile(r"^[RDCFE]-\d{3,4}$", re.IGNORECASE)
_KB_PATH = os.path.join(_BASE, "..", "knowledge", "criteria.json")


def kb_add_entry(id, category, title, tags, content, confirmed=False):
    if not confirmed:
        return {"refused": True,
                "reason": "未获用户确认。请先向用户复述完整条目(id/标题/tags/正文), 用户同意后再带confirmed=true写入"}
    # ---- 权限门: 知识库写入需人工确认弹窗(ask), LLM自报confirmed不能替代 ----
    detail = ("知识库沉淀新条目\nid: %s\n分类: %s\n标题: %s\ntags: %s\n正文: %s"
              % (id, category, str(title)[:60],
                 ",".join(str(t) for t in (tags if isinstance(tags, list) else [tags]))[:200],
                 str(content)[:400]))
    reason, ok = _perm.gate("kb_write", str(id), detail)
    if not ok:
        return {"refused": True, "reason": reason}
    id = str(id).strip()
    if not _ID_PAT.match(id):
        return {"error": "id格式应为 前缀-三位数字, 如R-015/C-011 (R判据/D缺陷/F指纹/C先例/E环境)"}
    if category not in ("rule", "defect", "fingerprint", "case", "env"):
        return {"error": "category非法"}
    if any(e["id"].upper() == id.upper() for e in _KB):
        return {"error": "id已存在: %s, 换一个编号" % id}
    if not os.path.exists(_KB_PATH):
        return {"error": "knowledge/criteria.json不存在, 请先按README从criteria.example.json复制创建"}
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,，\s]+", tags) if t]
    entry = {"id": id, "category": category, "title": str(title)[:60],
             "tags": [str(t)[:20] for t in tags][:12], "content": str(content)[:3000]}
    with open(_KB_PATH, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("entries", []).append(entry)
    with open(_KB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    _KB.append(entry)   # 即时生效
    return {"success": True, "entry_id": id, "total_entries": len(_KB),
            "note": "已写入knowledge/criteria.json并即时生效(kb_search立即可命中)"}


def _score_entries(query, limit=5):
    """单组关键词评分检索(原逻辑)"""
    tokens = [t for t in re.split(r"[\s,，。;；/、]+", query or "") if t]
    scored = []
    for e in _KB:
        score = 0
        for t in tokens:
            tu = t.upper()
            if tu == e["id"].upper():
                score += 4          # 判据编号精确命中(如"R-014"; 不再前缀误命中: 查R-1不会加到R-101)
            elif t in e["title"]:
                score += 3
            elif any(t in tag for tag in e.get("tags", [])):
                score += 3
            elif t in e["content"]:
                score += 1
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    return [{"id": e["id"], "category": e["category"], "title": e["title"],
             "content": e["content"]} for _, e in scored[:limit]]


def kb_search(query, limit=5):
    # 批量分诊: 分号/换行分隔多组关键词一次查(单组保持原返回结构)
    groups = [g for g in re.split(r"[;\n]", str(query or "")) if g.strip()]
    if len(groups) > 1:
        results = {}
        for g in groups[:10]:
            r = _score_entries(g, limit)
            if r:
                results[g.strip()[:60]] = r
        return {"batch": True, "matched_groups": len(results),
                "results": results,
                "note": "批量判据对照(每组≤%d条); 无命中的组说明知识库暂无对应判据, 按标准流程研判" % limit}
    entries = _score_entries(query, limit) if groups else []
    return {"matched": len(entries), "entries": entries}
