# -*- coding: utf-8 -*-
"""会话存储: logs/sessions/*.session.json
- 格式与旧版(TUI/GUI)完全兼容: 旧应用保存的历史会话可直接恢复
- 只在轮次边界保存(消息结构必然完整), 恢复时仍做防御性修复
"""
import json
import os
import sys
import time
import uuid

if getattr(sys, "frozen", False):
    BASE_DIR = ROOT = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))      # app/
    ROOT = os.path.dirname(BASE_DIR)                           # EZcode 项目根目录
SESSIONS_DIR = os.path.join(ROOT, "logs", "sessions")


def _ensure_dir():
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def new_id():
    # 秒级时间戳 + 随机后缀: 避免同一秒内连续新建会话产生同名覆盖
    return "session-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])


def _safe_sid(sid):
    return bool(sid) and all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in sid)


def save(sid, data):
    _ensure_dir()
    path = os.path.join(SESSIONS_DIR, sid + ".session.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    os.replace(tmp, path)          # 原子替换, 断电不产生半截文件


def load(sid):
    """返回 session dict 或 None。"""
    if not _safe_sid(sid):
        return None
    path = os.path.join(SESSIONS_DIR, sid + ".session.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def delete(sid):
    if not _safe_sid(sid):
        return False
    path = os.path.join(SESSIONS_DIR, sid + ".session.json")
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except Exception:
            return False
    return False


def list_sessions(limit=50):
    """磁盘会话摘要列表(按保存时间倒序)。"""
    out = []
    try:
        _ensure_dir()
        for fn in os.listdir(SESSIONS_DIR):
            if not fn.endswith(".session.json"):
                continue
            path = os.path.join(SESSIONS_DIR, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                n = len([m for m in (d.get("messages") or [])
                         if m.get("role") in ("user", "assistant")])
                if n == 0:
                    continue
                out.append({"id": fn[:-len(".session.json")],
                            "title": d.get("title") or "未命名会话",
                            "n_msgs": n,
                            "saved_at": d.get("saved_at", "") or
                                        time.strftime("%Y-%m-%d %H:%M:%S",
                                                      time.localtime(os.path.getmtime(path)))})
            except Exception:
                continue
    except Exception:
        pass
    out.sort(key=lambda x: x["saved_at"], reverse=True)
    return out[:limit]
