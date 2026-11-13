# -*- coding: utf-8 -*-
"""EZcode 评测集 (确定性回归评测)
Part A: 确定性单元测试(不调LLM, 秒级) —— 验证门逻辑/写操作拒绝/模式过滤/上下文压缩
Part B: golden评测(真实LLM, --llm开启) —— 提示词/判据改动后的行为回归
用法:
    python evals/run_evals.py           # Part A
    python evals/run_evals.py --llm     # Part A + Part B
"""
import sys, os, json, time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import agent as A
import verify
sys.path.insert(0, os.path.join(BASE, "app"))     # 桌面端引擎(专家记忆/界面偏好在 app/engine.py)
import engine as E
from tools import toolset, EXEC_TOOL_NAMES, configure, HANDLERS

PASS, FAIL = [], []


def check(case, ok, detail=""):
    (PASS if ok else FAIL).append(case)
    print("  %s %s %s" % ("✓" if ok else "✗", case, ("- " + detail[:80]) if not ok else ""))


def part_a():
    print("== Part A: 确定性单元测试 ==")
    cfg = A.load_config()
    configure(cfg)

    # A1: 验证门 G1 —— 高危误报定性无证据 = block
    tool_log = [{"name": "ngsoc_search_alarms", "args": "{}", "result_head": "{}"}]
    msgs = [{"role": "user", "content": "[SQL注入告警] 源1.2.3.4->目的198.51.100.5"}]
    g = verify.verify_round(tool_log, msgs, "研判结论: 误报, 高置信")
    check("A1-G1高危误报无证据被block", (not g["passed"]) and any(f["code"] == "G1/R-001" for f in g["findings"]))

    # A2: 验证门 G1 —— 有packet证据 = pass
    tool_log2 = tool_log + [{"name": "ngsoc_get_evidence", "args": "{}",
                             "result_head": '{"records":[{"packet_decoded":{"hexdump":"0000 "}}]}'}]
    g = verify.verify_round(tool_log2, msgs, "研判结论: 误报(有报文证据)")
    check("A2-G1有报文证据放行", g["passed"] or not any(f["code"] == "G1/R-001" for f in g["findings"]))

    # A3: 验证门 G2 —— 封禁建议无情报 = block
    g = verify.verify_round(tool_log, [{"role": "user", "content": "[SSH爆破] 源198.51.100.9"}],
                            "建议封禁该IP")
    check("A3-G2封禁缺情报被block", (not g["passed"]) and any(f["code"] == "G2/R-008" for f in g["findings"]))

    # A3b~A3d: 否定/声明性"封禁"不得被误判成封禁建议(G2是block级, 误伤会盖"结论未通过校验"章)
    for name, ans in (
        ("A3b-无封禁项不触发G2", "研判结论: 倾向误报，无需处置，无封禁项。"),
        ("A3c-封禁由用户手动执行不触发G2", "按R-008, 封禁由用户手动执行, 本研判仅登记建议。"),
        ("A3d-真实误报结论含无封禁项不触发G2",
         "倾向误报，建议找责任人确认14:23登录人后闭环，无封禁项。\n"
         "群回复版: 来源为内网worker节点, 无需封禁任何IP。"),
        ("A3e-旧版追加的校验横幅不被二次误判",
         "倾向误报，无封禁项。\n\n── 流程校验(确定性, 非模型自评) ──\n"
         "✗ BLOCK G2/R-008: 给出封禁建议但本轮无情报查询记录。")):
        g = verify.verify_round([], [{"role": "user", "content": "研判一条主机侧告警"}], ans)
        check(name, g["passed"] and not any(f["code"] == "G2/R-008" for f in g["findings"]))
    # A1b: "不是误报"不该被当成误报定性而触发G1
    g = verify.verify_round(tool_log,
                            [{"role": "user", "content": "[SQL注入告警] 源1.2.3.4"}],
                            "研判结论: 不是误报, 真阳性, 建议立即处置")
    check("A1b-否定误报表述不触发G1", not any(f["code"] == "G1/R-001" for f in g["findings"]))
    # A12: 防火墙/网络阻断类命令必须在权限层硬拒(封禁只给建议, 不允许shell绕道)
    from tools import permissions as _perm
    _perm.load_rules({})
    check("A12-New-NetFirewallRule被deny",
          _perm.check("shell", "New-NetFirewallRule -DisplayName block -RemoteAddress 1.2.3.4 -Action Block") == "deny")
    check("A12b-netsh advfirewall被deny",
          _perm.check("shell", "netsh advfirewall firewall add rule name=block dir=in action=block remoteip=1.2.3.4") == "deny")
    check("A12c-普通ping仍走ask未被误伤", _perm.check("shell", "ping 8.8.8.8") == "ask")

    # A4: 处置拒绝路径
    r = HANDLERS["ngsoc_dispose_alarms"](site="X站", alarm_uuid="fake", state=5,
                                         end_time=1, confirmed=False)
    check("A4-处置未确认被拒", r.get("refused") is True)

    # A5: kb_add_entry 未确认被拒
    r = HANDLERS["kb_add_entry"](id="X-999", category="rule", title="t", tags=["t"],
                                 content="c", confirmed=False)
    check("A5-沉淀未确认被拒", r.get("refused") is True)

    # A9: 写操作硬权限门——LLM带confirmed=true但无人工确认通道时, 必须拒绝
    # (安全语义回归: confirmed由LLM自填不构成授权, 人工确认弹窗是唯一防线)
    r = HANDLERS["ngsoc_dispose_alarms"](site="X站", alarm_uuid="fake", state=5,
                                         end_time=1, confirmed=True, desc="测试")
    check("A9-处置confirmed=true无人工通道仍被拒", r.get("refused") is True,
          str(r.get("reason"))[:80])
    r = HANDLERS["kb_add_entry"](id="X-999", category="rule", title="t", tags=["t"],
                                 content="c", confirmed=True)
    check("A9b-沉淀confirmed=true无人工通道仍被拒", r.get("refused") is True,
          str(r.get("reason"))[:80])

    # A10: 通用工具权限——web_fetch的SSRF硬防线(内网/回环拒绝) + file_edit无人工通道拒绝
    from tools import web as _web
    r = HANDLERS["web_fetch"]("http://127.0.0.1:8080/")
    check("A10-webfetch回环被SSRF防线拒", r.get("refused") is True, str(r)[:80])
    r = HANDLERS["web_fetch"]("http://169.254.169.254/")
    check("A10b-webfetch链路本地地址被拒", r.get("refused") is True, str(r)[:80])
    r = HANDLERS["file_edit"]("C:/tmp/x.txt", "a", "b")
    check("A10c-file_edit无人工通道被拒", r.get("refused") is True, str(r)[:80])
    # 通用只读工具在只读模式可见(写操作不可见)
    q = toolset("query")
    names = {t["function"]["name"] for t in q}
    check("A10d-只读模式含通用只读工具无写工具",
          {"fs_ls", "fs_glob", "fs_grep", "web_fetch", "web_search"} <= names
          and not ({"file_edit", "file_write", "shell"} & names))

    # A11: 批量分诊工具语义
    # kb_search 批量模式: 分号分隔多组一次查询(调用次数从N次降到1次)
    r = HANDLERS["kb_search"]("STRATUM 矿池; X-Forwarded-For", limit=3)
    check("A11-kb批量模式返回分组结果", r.get("batch") is True and isinstance(r.get("results"), dict)
          and len(r["results"]) >= 1, str(r)[:100])
    # kb_search 单查回归(空格分隔仍是单组, 结构不变)
    r = HANDLERS["kb_search"]("R-009 国外 封禁")
    check("A11b-kb单查回归", r.get("batch") is None and r.get("matched", 0) >= 1
          and any(e["id"] == "R-009" for e in r.get("entries", [])), str(r)[:100])
    # netbox 批量IP: 混合分隔符拆分正确(注: 会发真实资产库查询, 依赖devices.json配置;
    # 失败只影响本条, 不影响批量结构断言的前半部分)
    from tools import netbox as _nb
    r = _nb.lookup_ip("8.8.8.8; 1.1.1.1, 9.9.9.9")
    check("A11c-netbox批量IP走batch结构", r.get("batch") is True and r.get("count") == 3,
          str(r)[:100])
    # edr 批量排序纯函数(不依赖外部edr_client模块)
    from tools import edr as _edr
    fake = [{"base_info": {"severity": "LOW", "detection_code": "QT-D-aaa",
                           "detection_time": "2026-11-05T01:00:00Z", "status": "PENDING",
                           "detection_title": "t1"}},
            {"base_info": {"severity": "CRITICAL", "detection_code": "QT-D-bbb",
                           "detection_time": "2026-11-05T01:00:00Z", "status": "PENDING",
                           "detection_title": "t2"}}]
    out = _edr._sort_and_cap(fake)
    check("A11d-edr批量结果按严重度排序", out["total"] == 2
          and out["alarms"][0]["severity"] == "CRITICAL")

    # A6: 模式过滤
    q = toolset("query")
    check("A6-只读模式无写工具", not any(t["function"]["name"] in EXEC_TOOL_NAMES for t in q))

    # A13: 设备无关能力判定(caps.py注册表驱动) —— 接入新安全设备时verify零改动
    import caps as _caps
    _caps.CAPABILITIES["test_intel_x"] = ["intel"]
    _caps.CAPABILITIES["test_asset_x"] = ["asset"]
    try:
        g = verify.verify_round([{"name": "test_intel_x", "args": "{}", "result_head": "{}"}],
                                msgs, "建议封禁 1.2.3.4")
        check("A13-运行时注册的情报源自动认账",
              not any(f["code"] == "G2/R-008" for f in g["findings"]))
        g = verify.verify_round(
            [{"name": "test_asset_x", "args": "查询 8.8.4.4", "result_head": "{}"}],
            [{"role": "user",
              "content": "[SSH爆破告警] 源 8.8.4.4 和 5.5.5.5 -> 198.51.100.5:22, n=12次, 站点demo"}],
            "研判结论待确认")
        g4 = [f for f in g["findings"] if f["code"] == "G4/纪律3"]
        check("A13b-新资产工具核查过的IP不误报", not any("8.8.4.4" in f["text"] for f in g4))
        check("A13c-未核查的IP仍被G4提醒", any("5.5.5.5" in f["text"] for f in g4))
    finally:
        _caps.CAPABILITIES.pop("test_intel_x", None)
        _caps.CAPABILITIES.pop("test_asset_x", None)

    # A14: 系统提示词设备无关 —— 工具细节靠环境信息动态注入, 提示词正文不点名设备工具
    p = open(os.path.join(BASE, "prompts", "system.md"), encoding="utf-8").read()
    check("A14-提示词不点名安全工具",
          "ngsoc_search_alarms" not in p and "threatbook_ip" not in p and "netbox_lookup" not in p)
    check("A14b-方法论与纪律保留", "证据分级" in p and "不主动推进处置" in p
          and "提示注入防御" in p)

    # A15: 专家记忆 —— 启动时的专家由 devices.json 的 ui.persona 决定
    check("A15-未配置时默认安全运营", E._ui_persona({}) == "ezcode")
    check("A15b-记住上次使用的专家(日常工作助手)",
          E._ui_persona({"ui": {"persona": "assistant"}}) == "assistant")
    check("A15c-专家已不存在时回落安全运营",
          E._ui_persona({"ui": {"persona": "deleted-persona"}}) == "ezcode")

    # A16: 文档读取 —— docx/xlsx/pptx 是压缩包, 必须能取出文字(不是二进制乱码)
    import zipfile, tempfile
    from tools import docs as _docs
    _d = os.path.join(tempfile.mkdtemp(), "sample.docx")
    with zipfile.ZipFile(_d, "w") as _z:
        _z.writestr("word/document.xml",
                    "<w:document><w:body>"
                    "<w:p><w:r><w:t>第一段: 告警研判</w:t></w:r></w:p>"
                    "<w:p><w:r><w:t>第二段: 处置建议</w:t></w:r></w:p>"
                    "</w:body></w:document>")
    _r = _docs.read(_d)
    check("A16-docx 文字抽取(段落完整, 无XML标签)",
          "第一段: 告警研判" in _r.get("text", "")
          and "第二段: 处置建议" in _r.get("text", "")
          and "<w:" not in _r.get("text", ""), str(_r)[:90])
    from tools import CAPABILITIES as _CAPS
    check("A16b-doc_read 已注册且属文档能力",
          any(t["function"]["name"] == "doc_read" for t in A.TOOLS)
          and "office" in (_CAPS.get("doc_read") or []),
          str(_CAPS.get("doc_read")))
    _bin = os.path.join(os.path.dirname(_d), "sample.bin")
    open(_bin, "wb").write(b"\x00\x01")
    check("A16c-非文档扩展名给出明确提示",
          "不支持的文档类型" in str(_docs.read(_bin)))

    # A17: 权限门回归 —— 无确认通道时 shell/python 一律不执行(逐次确认的底线)
    import tools.permissions as _pp
    _pp.set_confirm(None)
    check("A17-无确认通道时 shell 被拒(必须人工确认)",
          _pp.gate("shell", "ping 127.0.0.1")[1] is False)
    check("A17b-破坏性命令直接 deny",
          _pp.gate("shell", "reg query HKCU")[1] is False)

    # A18~A21: 大文档事故回归(2026-11-11, API配置文档.docx 306365字, 空转1275秒无结论)
    # 根因: doc_read 只能从头读且被引擎24000字硬截断; 提示词宣称只读模式下不存在的
    # shell/run_python 可用; 无重复调用拦截、无时间预算, 模型层层派生子代理绕圈。
    import tools.docs as _docs2
    _big = os.path.join(tempfile.mkdtemp(), "big_api_doc.txt")
    _lines = ["第%05d行 接口参数与返回值示例 filler filler。\n" % i for i in range(3000)]
    _lines[0] = "第2章 API校验机制: ApiKey+Timestamp, HmacSHA256+Base64 签名。\n"
    _lines[2500] = "3.1.2 威胁-告警主机: GET /api/v1/alert/host UNIQUE_MARK_2500\n"
    open(_big, "w", encoding="utf-8").write("".join(_lines))
    _r1 = _docs2.read(_big)
    _js1 = json.dumps(_r1, ensure_ascii=False)
    check("A18-大文档默认结果不被引擎二次截断, 且给出下一页 offset",
          len(_js1) < E.TOOL_RESULT_LIMIT and _r1.get("next_offset") == len(_r1["text"])
          and "不要反复加大 max_chars" in _r1.get("hint", ""),
          "json=%d next=%s" % (len(_js1), _r1.get("next_offset")))
    _r2 = _docs2.read(_big, offset=_r1["next_offset"])
    check("A18b-offset 翻页读到的是后续内容",
          _r2.get("offset") == _r1["next_offset"] and _r2["text"]
          and _r2["text"] != _r1["text"][:len(_r2["text"])],
          "offset=%s" % _r2.get("offset"))
    _rf = _docs2.read(_big, find="UNIQUE_MARK_2500")
    check("A18c-find 关键词能定位正文深章节(跳过目录命中)",
          any(m["pos"] > _r1["next_offset"] for m in _rf.get("matches", []))
          and "UNIQUE_MARK_2500" in _rf["text"],
          str(_rf.get("matches"))[:100])
    check("A18d-元信息排在 text 之前(截断也看得见 hint)",
          _js1.index('"next_offset"') < _js1.index('"text"')
          and _js1.index('"hint"') < _js1.index('"text"'))
    _mq = "\n".join(E._capability_manifest("coder", "query"))
    _me = "\n".join(E._capability_manifest("coder", "execute"))
    check("A19-只读模式能力清单不含写/执行工具",
          all(x not in _mq for x in ("shell", "run_python", "office_edit")), _mq[:120])
    check("A19b-可执行模式能力清单含写/执行工具",
          all(x in _me for x in ("shell", "run_python", "office_edit")), _me[:120])
    _pq = E.build_system_prompt(cfg, "coder", "query")
    check("A19c-只读提示词明确写出这些工具不可调用",
          "不在可调用工具清单里" in _pq and "子代理同样是只读" in _pq)
    check("A19d-系统提示词含'信息够了就立刻作答'的收敛纪律",
          "立刻给结论" in _pq and "时间预算" in _pq)
    _tc = lambda args: {"function": {"name": "doc_read", "arguments": json.dumps(args)}}
    check("A20-重复调用键: 只改 max_chars 视为同一次调用",
          E.tool_call_key(_tc({"path": "x.docx", "max_chars": 30000}))
          == E.tool_call_key(_tc({"path": "x.docx", "max_chars": 320000})))
    check("A20b-换 offset/find 视为不同调用(允许翻页)",
          E.tool_call_key(_tc({"path": "x.docx", "offset": 12000}))
          != E.tool_call_key(_tc({"path": "x.docx", "offset": 24000}))
          and E.tool_call_key(_tc({"path": "x.docx", "find": "告警主机"}))
          != E.tool_call_key(_tc({"path": "x.docx"})))
    check("A21-单轮与子代理都有时间预算(主>子)",
          0 < E.SUBAGENT_BUDGET < E.TURN_BUDGET,
          "main=%s sub=%s" % (E.TURN_BUDGET, E.SUBAGENT_BUDGET))

    # A22: opencode 式内核能力 —— 文档内检索 / 章节索引 / 逐次确认闭环
    from tools import fs as _fs
    _od = os.path.join(os.path.dirname(_d), "outline.docx")
    _paras = ["1 接口概述", "1.1 背景", "2 API校验机制", "2.1 请求签名",
              "3.1.2 威胁-告警主机", "接口路径 /api/v1/host/getFallHostSumList"]
    with zipfile.ZipFile(_od, "w") as _z:
        _z.writestr("word/document.xml", "<w:document><w:body>" + "".join(
            "<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % p for p in _paras)
            + "</w:body></w:document>")
    _g = _fs.fs_grep("getFallHostSumList", path=_od, context=1)
    check("A22-fs_grep 能直接检索 docx 内部文字(不再当二进制跳过)",
          _g.get("matched", 0) >= 1, str(_g)[:110])
    if _g.get("matches"):
        _pos = _g["matches"][0]["pos"]
        _seg = _docs.read(_od, offset=_pos, max_chars=400)
        check("A22b-grep 的 pos 可直接当 doc_read 的 offset 精读",
              "getFallHostSumList" in _seg["text"], _seg["text"][:90])
    _ol = _docs.read(_od, outline=True)
    check("A22c-章节索引给出标题+字符位置",
          _ol.get("outline") is True and _ol.get("sections", 0) >= 4
          and any("3.1.2" in l for l in _ol["text"].splitlines()), str(_ol)[:110])
    check("A22d-outline 与顺序读不算重复调用(去重键已区分)",
          E.tool_call_key(_tc({"path": "x.docx", "outline": True}))
          != E.tool_call_key(_tc({"path": "x.docx"})))
    _sg = next(t for t in A.TOOLS if t["function"]["name"] == "fs_grep")
    check("A22e-fs_grep schema 含 context 且声明可搜文档",
          "context" in _sg["function"]["parameters"]["properties"]
          and "Word/Excel/PPT/PDF" in _sg["function"]["description"])
    _pd = E.build_system_prompt(cfg, "coder", "query")
    check("A22f-提示词含大文档三步走纪律",
          "outline=true" in _pd and "fs_grep" in _pd and "offset=<pos>" in _pd)
    _nq = {t["function"]["name"] for t in toolset("query", "coder")}
    _ne = {t["function"]["name"] for t in toolset("execute", "coder")}
    check("A22g-只读无 shell/run_python, 可执行模式才解锁",
          not ({"shell", "run_python"} & _nq) and {"shell", "run_python"} <= _ne,
          "query=%s" % sorted({"shell", "run_python"} & _nq))
    _pp.load_rules({})          # 清掉会话级"总是允许", 验证逐次确认
    _seen = []
    _pp.set_confirm(lambda a, r, d: (_seen.append(r), True)[1])
    _pp.gate("shell", "ping 127.0.0.1")
    _pp.gate("shell", "ping 127.0.0.1")
    _pp.set_confirm(None)
    check("A22h-同一命令每次执行都重新弹人工确认(不缓存放行)",
          len(_seen) == 2, str(_seen))

    # A23: 多任务隔离 —— "本会话总是允许"不能跨任务串, 后台确认不能干等超时
    _pp.load_rules({})
    _pp.grant_session("shell", "echo *", scope="taskA")
    check("A23-本会话总是允许 只对授权的任务生效",
          _pp.check("shell", "echo hi", scope="taskA") == "allow")
    check("A23b-另一个任务不继承该授权(仍逐次确认)",
          _pp.check("shell", "echo hi", scope="taskB") == "ask")
    check("A23c-权限层支持按会话解析 scope", hasattr(_pp, "set_scope"))
    check("A23d-权限确认超时足够后台任务弹窗(不再120s就判拒绝)",
          E.CONFIRM_TIMEOUT >= 300, E.CONFIRM_TIMEOUT)
    check("A23e-确认事件带来源任务标题(弹窗可标注是哪个任务)",
          "title" in E.Engine._confirm_gate.__code__.co_consts)
    check("A23f-后台任务结束会打未读标记(任务列表可见)",
          "unread" in E.Engine._emit_safe.__code__.co_consts)
    def _cc(fn):
        """co_consts 里 dict 字面量的键在3.11+会被打包成KW_NAMES元组, 需展平。"""
        out = []
        for c in fn.__code__.co_consts:
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, (tuple, frozenset)):
                out += [x for x in c if isinstance(x, str)]
        return out
    check("A23g-任务列表返回后台阶段与未读态",
          "stage" in _cc(E.Engine.list_sessions)
          and "unread" in _cc(E.Engine.list_sessions))

    # A7: 上下文压缩
    messages = [{"role": "system", "content": "s"},
                {"role": "user", "content": "u"}]
    big = "x" * 3000
    for i in range(60):
        messages.append({"role": "tool", "tool_call_id": str(i), "content": big})
    n = A._compact_messages(messages)
    total = sum(len(str(m.get("content") or "")) for m in messages)
    check("A7-上下文超预算自动压缩", n and total <= A.CONTEXT_CHAR_BUDGET,
          "compacted=%s total=%d" % (n, total))

    # A8: 消息修复
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}]
    A.repair_messages(msgs)
    check("A8-中止后无悬挂tool_calls", msgs[-1]["role"] == "user")


class _SilentUI:
    import contextlib

    @contextlib.contextmanager
    def tool_running(self, fname):
        yield

    def __getattr__(self, k):
        def noop(*a, **kw):
            pass
        return noop


def part_b():
    print()
    print("== Part B: golden评测(真实LLM) ==")
    cfg = A.load_config()
    configure(cfg)
    profiles, active = A.normalize_llm(cfg)
    cases = json.load(open(os.path.join(BASE, "evals", "golden.json"), encoding="utf-8"))["cases"]
    for c in cases:
        slog = A.SessionLog()
        messages = [{"role": "system", "content": A.build_system_prompt(cfg)},
                    {"role": "user", "content": c["prompt"]}]
        try:
            answer, *_ = A.run_agent(profiles[active], active, messages, slog, _SilentUI(),
                                     tools_list=toolset("query"))
        except Exception as e:
            check(c["id"], False, "异常: %s" % e)
            continue
        bad = [k for k in c["must_contain"] if k not in answer]
        bad2 = [k for k in c.get("must_not_contain", []) if k in answer]
        detail = "缺关键词:%s 命中禁词:%s" % (bad, bad2)
        # require_tools: 该用例必须真实执行过工具且无错误(防止靠系统提示词内容蒙混)
        if c.get("require_tools"):
            log_txt = open(slog.path, encoding="utf-8").read()
            if '-> {"error"' in log_txt:
                check(c["id"], False, "工具调用报错: " + detail)
                continue
            if "工具" not in log_txt.replace("工具调用上限", ""):
                check(c["id"], False, "未调用任何工具: " + detail)
                continue
        check(c["id"], not bad and not bad2, detail)


def main():
    t0 = time.time()
    part_a()
    if "--llm" in sys.argv:
        part_b()
    print()
    print("结果: %d通过 / %d失败 (用时%.1fs)" % (len(PASS), len(FAIL), time.time() - t0))
    if FAIL:
        print("失败用例:", [c for c in FAIL])
        sys.exit(1)


if __name__ == "__main__":
    main()
