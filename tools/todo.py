# -*- coding: utf-8 -*-
"""内置轻量待办清单(纯本地): todo_list / todo_add / todo_done / todo_remove。
定位: 日常工作助手的"记事本"——帮用户记任务、列待办、标完成, 跨会话保留。
存储: ~/.ezcode/todos.json (应用自管数据文件, 不触碰用户文件系统)。
风险归类: 写范围仅限本清单文件, 不进 EXEC_TOOL_NAMES —— 只读模式下也能记任务(类似便签),
且安全运营专家视野同样可见(用户说"记一下"时不该被拦)。
"""
import json
import os
import time

_STORE = os.path.join(os.path.expanduser("~"), ".ezcode", "todos.json")
MAX_TODOS = 500          # 清单上限(防无上限增长)
_MAX_LEN = 200           # 单条任务文本上限


def configure(cfg):
    """接口对齐(注册/注销由 tools.apply_builtin_tools 统一驱动)。"""
    pass


def _load():
    try:
        with open(_STORE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items):
    d = os.path.dirname(_STORE)
    os.makedirs(d, exist_ok=True)
    tmp = _STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _STORE)


def _next_id(items):
    return max([int(it.get("id") or 0) for it in items] or [0]) + 1


def _fmt(it):
    out = {"id": it.get("id"), "title": it.get("title", ""),
           "created": it.get("created", ""), "done": bool(it.get("done"))}
    if it.get("due"):
        out["due"] = it["due"]
    if it.get("note"):
        out["note"] = it["note"][:120]
    if it.get("done_at"):
        out["done_at"] = it["done_at"]
    return out


# ---------------- 工具1: 列待办 ----------------
SCHEMA_LIST = {
    "type": "function",
    "function": {
        "name": "todo_list",
        "description": "查看本地待办清单(跨会话保留)。可按状态(待办/已完成)与关键词过滤, 按创建时间排序。",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["open", "done", "all"],
                       "description": "open=未完成(默认), done=已完成, all=全部"},
            "keyword": {"type": "string", "description": "标题/备注包含过滤词, 可选"},
        }},
    },
}


def todo_list(status="open", keyword=""):
    items = _load()
    st = str(status or "open").lower()
    kw = str(keyword or "").strip().lower()
    rows = []
    for it in items:
        if st == "open" and it.get("done"):
            continue
        if st == "done" and not it.get("done"):
            continue
        if kw and kw not in (str(it.get("title", "")) + " " + str(it.get("note", ""))).lower():
            continue
        rows.append(_fmt(it))
    rows.sort(key=lambda x: (x.get("created") or ""), reverse=True)
    return {"todos": rows, "total_open": sum(1 for it in items if not it.get("done")),
            "note": "共%d条" % len(rows)}


# ---------------- 工具2: 记任务 ----------------
SCHEMA_ADD = {
    "type": "function",
    "function": {
        "name": "todo_add",
        "description": "把任务记入本地待办清单(可一次记多条, 一行一条)。适合用户口头交代的事项、"
                       "后续要做的事、约定的时间点。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "任务内容, 多条任务一行一条"},
            "note": {"type": "string", "description": "补充说明, 可选"},
            "due": {"type": "string", "description": "约定时间/期限(自由文本, 如 '明天上午' '周五前'), 可选"},
        }, "required": ["title"]},
    },
}


def todo_add(title, note="", due=""):
    lines = [ln.strip() for ln in str(title or "").replace("\r", "").split("\n") if ln.strip()]
    if not lines:
        return {"error": "任务内容为空"}
    items = _load()
    if len(items) >= MAX_TODOS:
        return {"error": "待办清单已满(%d条), 请先清理已完成项" % MAX_TODOS}
    nid = _next_id(items)
    now = time.strftime("%Y-%m-%d %H:%M")
    added = []
    for ln in lines:
        it = {"id": nid, "title": ln[:_MAX_LEN], "created": now, "done": False}
        if str(note or "").strip():
            it["note"] = str(note).strip()[:_MAX_LEN]
        if str(due or "").strip():
            it["due"] = str(due).strip()[:60]
        items.append(it)
        added.append(nid)
        nid += 1
    _save(items)
    return {"ok": True, "ids": added, "added": len(added),
            "note": "已记录%d条待办(id: %s)" % (len(added), ", ".join(str(x) for x in added))}


# ---------------- 工具3: 标完成 ----------------
SCHEMA_DONE = {
    "type": "function",
    "function": {
        "name": "todo_done",
        "description": "把待办标记为已完成(按编号精确或关键词模糊匹配)。做完了的事用它销账。",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "number", "description": "待办编号(优先使用)"},
            "keyword": {"type": "string", "description": "没有编号时按关键词匹配标题, 恰好一条才执行"},
        }},
    },
}


def todo_done(id=None, keyword=""):
    items = _load()
    kw = str(keyword or "").strip().lower()
    hits = []
    for it in items:
        if not it.get("done"):
            if id is not None and int(it.get("id") or 0) == int(id):
                hits.append(it)
            elif kw and not id and kw in str(it.get("title", "")).lower():
                hits.append(it)
    if not hits:
        return {"error": "没找到匹配的未完成待办(id=%r, keyword=%r)" % (id, keyword)}
    if len(hits) > 1:
        return {"error": "匹配到%d条, 请指定编号: %s"
                % (len(hits), ", ".join("#%s %s" % (it.get("id"), it.get("title")) for it in hits[:8]))}
    hits[0]["done"] = True
    hits[0]["done_at"] = time.strftime("%Y-%m-%d %H:%M")
    _save(items)
    return {"ok": True, "id": hits[0].get("id"),
            "note": "已完成: #%s %s" % (hits[0].get("id"), hits[0].get("title"))}


# ---------------- 工具4: 删除 ----------------
SCHEMA_REMOVE = {
    "type": "function",
    "function": {
        "name": "todo_remove",
        "description": "从待办清单删除一条(记错了/不需要了)。按编号精确或关键词唯一匹配。",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "number", "description": "待办编号(优先使用)"},
            "keyword": {"type": "string", "description": "没有编号时按关键词匹配标题, 恰好一条才执行"},
        }},
    },
}


def todo_remove(id=None, keyword=""):
    items = _load()
    kw = str(keyword or "").strip().lower()
    hits = [it for it in items
            if (id is not None and int(it.get("id") or 0) == int(id))
            or (kw and not id and kw in str(it.get("title", "")).lower())]
    if not hits:
        return {"error": "没找到匹配的待办(id=%r, keyword=%r)" % (id, keyword)}
    if len(hits) > 1:
        return {"error": "匹配到%d条, 请指定编号: %s"
                % (len(hits), ", ".join("#%s %s" % (it.get("id"), it.get("title")) for it in hits[:8]))}
    items.remove(hits[0])
    _save(items)
    return {"ok": True, "id": hits[0].get("id"),
            "note": "已删除: #%s %s" % (hits[0].get("id"), hits[0].get("title"))}
