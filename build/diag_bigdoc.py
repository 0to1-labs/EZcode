# -*- coding: utf-8 -*-
"""大文档事故(2026-11-11, 306365字 docx, 空转1275秒无结论)的端到端回归诊断。

不调真实 LLM: 用脚本化的 stream_chat 复现模型"反复用不同 max_chars 读同一段",
验证引擎现在会:
  1) 拦截第二次同段重读, 不再把时间耗在拿不到新信息的调用上
  2) 时间预算到时强制收尾, 一定给用户一个(阶段性)答案
  3) doc_read 的分页/关键词定位在真实大文档上可用, 且结果不会被引擎二次截断
用法: py -3 build/diag_bigdoc.py [可选: 大文档路径]
"""
import json
import os
import sys
import tempfile
import threading
import types

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "app"))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import engine as E                      # noqa: E402
from tools import configure, docs       # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s %s %s" % ("✓" if ok else "✗", name,
                          ("- " + str(detail)[:120]) if not ok else ""))


class Slog:
    def log(self, *a, **kw):
        pass


class FakeEngine:
    """不跑 Engine.__init__(会加载配置/注册MCP), 只提供 _agent_loop 需要的属性。"""

    def __init__(self, cfg, system="s"):
        self.cfg = cfg
        self.persona = "coder"
        self.mode = "query"
        self.lang = "zh"
        self.profile = {}
        self.active = "mock"
        self.cancel = threading.Event()
        self.slog = Slog()
        self.sess = {"messages": [{"role": "system", "content": system},
                                  {"role": "user", "content": "这个大文档里的 API 怎么配置?"}],
                     "tool_log": []}
        self.system_prompt = system
        self.events = []
        self._exec_tool = types.MethodType(E.Engine._exec_tool, self)
        self._dispatch_subagent = types.MethodType(E.Engine._dispatch_subagent, self)

    def _emit_safe(self, payload):
        self.events.append(payload)


def tool_msg(name, args, i):
    return ({"role": "assistant", "content": None,
             "tool_calls": [{"id": "c%d" % i,
                             "function": {"name": name,
                                          "arguments": json.dumps(args, ensure_ascii=False)}}]},
            0, None)


def scripted(script):
    it = iter(script)
    seen = []

    def f(profile, active, msgs, tools, cancel=None, on_reason=None, on_content=None):
        seen.append({"n_tools": len(tools or []),
                     "last": (msgs[-1].get("content") or "")[:120]})
        item = next(it)
        return item(tools, msgs) if callable(item) else item

    return f, seen


def make_big_text():
    p = os.path.join(tempfile.mkdtemp(), "big_api_doc.txt")
    lines = ["第%05d行 接口参数与返回值示例 filler filler。\n" % i for i in range(3000)]
    lines[0] = "第2章 API校验机制: ApiKey+Timestamp, HmacSHA256+Base64 签名。\n"
    lines[2500] = "3.1.2 威胁-告警主机: GET /api/v1/alert/host UNIQUE_MARK_2500\n"
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(lines))
    return p


def main():
    doc = sys.argv[1] if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]) else make_big_text()
    cfg = E.load_config()
    configure(cfg)
    _orig = E.L.stream_chat

    print("== 1. 复现'反复加大 max_chars 重读同一段' ==")
    try:
        script = [tool_msg("doc_read", {"path": doc, "max_chars": 30000}, 1),
                  tool_msg("doc_read", {"path": doc, "max_chars": 320000}, 2),
                  ({"role": "assistant", "content": "按文档第2章: 用 ApiKey+Timestamp 做 HmacSHA256+Base64 签名即可接入。"},
                   0, None)]
        E.L.stream_chat, seen = scripted(script)
        fake = FakeEngine(cfg)
        answer, n_tools, _ = E.Engine._agent_loop(fake, fake.sess["messages"], is_sub=False)
        heads = [x["result_head"] for x in fake.sess["tool_log"]]
        check("第二次同段重读被拦截(不产生新的大结果)",
              len(heads) >= 2 and "重复调用被拦截" in heads[1], heads[-1][:100] if heads else "no tools")
        check("拦截后本轮正常给出结论, 不是空转或'已达上限'",
              "HmacSHA256" in answer and "已达单轮工具调用上限" not in answer,
              answer[:100])
        check("没有把同一段结果重复灌进上下文",
              len(seen) == 3, "llm_rounds=%d" % len(seen))
    finally:
        E.L.stream_chat = _orig

    print("== 2. 时间预算到达时强制收尾 ==")
    try:
        cfg2 = dict(cfg)
        cfg2["ui"] = dict(cfg.get("ui") or {})
        cfg2["ui"]["turn_budget"] = 0
        E.L.stream_chat, seen2 = scripted([
            tool_msg("doc_read", {"path": doc}, 1)])   # 模型还想调工具, 应被引擎掐掉
        fake2 = FakeEngine(cfg2)
        answer2, _, _ = E.Engine._agent_loop(fake2, fake2.sess["messages"], is_sub=False)
        check("预算到达后下发给模型的工具列表为空", seen2[0]["n_tools"] == 0, seen2[0])
        check("预算到达后仍然返回文本, 不让用户干等",
              isinstance(answer2, str) and answer2.strip()
              and "时间预算已到" in answer2, answer2[:100])
        check("预算说明已注入最后一轮请求", "时间预算已到" in seen2[0]["last"], seen2[0]["last"])
    finally:
        E.L.stream_chat = _orig

    print("== 3. 真实/模拟大文档的分页与关键词定位 ==")
    r1 = docs.read(doc)
    s1 = json.dumps(r1, ensure_ascii=False)
    check("默认结果整体小于引擎 24000 字截断线", len(s1) < E.TOOL_RESULT_LIMIT,
          "json=%d" % len(s1))
    check("元信息在正文之前, 截断也看得到 next_offset/hint",
          '"next_offset"' in s1 and s1.index('"hint"') < s1.index('"text"'))
    check("第一段就含有回答 API 配置所需的签名机制",
          any(k in r1["text"] for k in ("API校验机制", "HmacSHA256", "请求签名")))
    kw = "威胁-告警主机" if "API配置文档" in os.path.basename(doc) else "UNIQUE_MARK_2500"
    rf = docs.read(doc, find=kw)
    check("find 能定位到第一段之后的深章节",
          any(m["pos"] > (r1.get("next_offset") or 0) for m in rf.get("matches", [])),
          str(rf.get("matches"))[:120])
    if rf.get("matches"):
        pos = rf["matches"][-1]["offset"]
        ro = docs.read(doc, offset=pos, max_chars=2000)
        check("按 matches 给的 offset 能读到该章节正文",
              kw in ro["text"] or "告警主机" in ro["text"], ro["text"][:100])

    print("== 4. opencode 式检索: fs_grep 直搜文档 → doc_read 精读 ==")
    from tools import fs as _fs
    _g = _fs.fs_grep(kw, path=doc, limit=3, context=1)
    check("fs_grep 能直接检索文档(真实 docx 自动解包)", _g.get("matched", 0) >= 1,
          str(_g)[:120])
    if _g.get("matches"):
        _gp = _g["matches"][-1]["pos"]
        _gd = docs.read(doc, offset=_gp, max_chars=1500)
        check("fs_grep 的 pos 直接当 doc_read 的 offset 能读到该章节",
              kw in _gd["text"] or "告警主机" in _gd["text"] or "MARK" in _gd["text"],
              _gd["text"][:100])
    _ol = docs.read(doc, outline=True)
    check("outline 给出章节索引且结果不被引擎截断",
          _ol.get("outline") is True and _ol.get("sections", 0) >= 1
          and len(json.dumps(_ol, ensure_ascii=False)) < E.TOOL_RESULT_LIMIT,
          "sections=%s" % _ol.get("sections"))

    print("\n大文档事故诊断: %d通过 / %d失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
