# -*- coding: utf-8 -*-
"""多任务并行回归诊断: 每个任务独立上下文/专家/模型/模式/运行状态, 事件带 sid 不串台。

不调真实 LLM: 脚本化 stream_chat 模拟"耗时的一轮", 让任务A还在跑时就新建任务B,
验证 A 的输出、专家、模型、中止信号全都不会落到 B 上。
用法: py -3 build/diag_sessions.py
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "app"))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import engine as E                       # noqa: E402
import store                             # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s %s %s" % ("✓" if ok else "✗", name,
                          ("- " + str(detail)[:130]) if not ok else ""))


def wait_idle(eng, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not any(c["busy"] for c in eng._ctxs.values()):
            return True
        time.sleep(0.04)
    return False


def users(ctx):
    return [m["content"] for m in ctx["sess"]["messages"] if m.get("role") == "user"]


def main():
    events = []
    _orig_stream, _orig_oneshot = E.L.stream_chat, E.L.oneshot

    def fake_stream(profile, active, msgs, tools, cancel=None,
                    on_reason=None, on_content=None):
        time.sleep(0.35)                 # 保证 A 还没结束就能切到 B
        return ({"role": "assistant", "content": "本轮完成", "tool_calls": None}, 0, None)

    E.L.stream_chat = fake_stream
    E.L.oneshot = lambda *a, **kw: "并行任务"
    try:
        eng = E.Engine(emit=lambda p: events.append(p), cfg=E.load_config())
        eng.profiles["mock2"] = {"model": "mock-model-2", "context": 128000}

        print("== 1. 运行中新建第二个任务 ==")
        sidA = eng.sid
        ctxA = eng._ctxs[sidA]
        personaA, modelA, modeA = ctxA["persona"], ctxA["active"], ctxA["mode"]
        check("任务A已建立", sidA in eng._ctxs and eng.state()["sid"] == sidA)
        check("A发送成功", eng.send("任务A: 分析告警").get("ok") is not False)
        time.sleep(0.08)
        check("A进入运行中", ctxA["busy"] is True)
        r = eng.new_session()
        check("运行中仍可新建任务(不再被busy拒绝)", "error" not in r, r)
        sidB = r.get("sid")
        check("新任务是另一个上下文", sidB and sidB != sidA and sidB in eng._ctxs, r)

        print("== 2. 新任务独立切专家/模型/模式 ==")
        p = eng.set_persona("coder")
        check("新任务可切换专家", "error" not in p, p)
        m = eng.set_model("mock2")
        check("新任务可切换模型", "error" not in m, m)
        md = eng.set_mode("execute")
        check("新任务可切换执行模式", "error" not in md, md)
        ctxB = eng._ctxs[sidB]
        check("B的专家/模型/模式已变更",
              ctxB["persona"] == "coder" and ctxB["active"] == "mock2"
              and ctxB["mode"] == "execute",
              (ctxB["persona"], ctxB["active"], ctxB["mode"]))
        check("A的专家/模型/模式未受污染",
              ctxA["persona"] == personaA and ctxA["active"] == modelA
              and ctxA["mode"] == modeA,
              (ctxA["persona"], ctxA["active"], ctxA["mode"]))
        with eng._bound(ctxA):
            pa = eng.set_persona("assistant")
        check("运行中的任务A禁止切换专家", "error" in pa, pa)

        print("== 3. 两个任务并发跑完, 消息与事件互不串台 ==")
        check("B在A运行期间也能发送", eng.send("任务B: 写个脚本").get("ok") is not False)
        check("两轮都已收尾", wait_idle(eng))
        ua, ub = users(ctxA), users(ctxB)
        check("A只含A的用户消息", ua == ["任务A: 分析告警"], ua)
        check("B只含B的用户消息", ub == ["任务B: 写个脚本"], ub)
        aa = [m["content"] for m in ctxA["sess"]["messages"] if m.get("role") == "assistant"]
        ab = [m["content"] for m in ctxB["sess"]["messages"] if m.get("role") == "assistant"]
        check("两边各自收到自己的回答", len(aa) == 1 and len(ab) == 1, (aa, ab))
        rounds = [e for e in events if e.get("t") == "round_end"]
        check("每个事件都带 sid", all(e.get("sid") for e in events),
              [e.get("t") for e in events[:6]])
        check("round_end 分别属于 A/B",
              {e.get("sid") for e in rounds} == {sidA, sidB},
              [e.get("sid") for e in rounds])

        print("== 4. 切回与持久化 ==")
        eng.open_session(sidA)
        check("切回A恢复A的专家与模型",
              eng.state()["persona"] == personaA and eng.state()["active"] == modelA,
              (eng.state()["persona"], eng.state()["active"]))
        eng.open_session(sidB)
        check("切回B恢复B的专家与模型",
              eng.state()["persona"] == "coder" and eng.state()["active"] == "mock2",
              (eng.state()["persona"], eng.state()["active"]))
        lst = eng.list_sessions()
        byid = {x["id"]: x for x in lst}
        check("任务列表含两个任务且带专家/模型/激活态",
              sidA in byid and sidB in byid
              and byid[sidB].get("persona") == "coder"
              and byid[sidB].get("active") is True
              and byid[sidA].get("active") is False,
              [(x["id"], x.get("persona"), x.get("active")) for x in lst[:4]])
        eng._save()
        d = store.load(sidB)
        check("会话文件落盘了本任务的专家/模型/模式",
              bool(d) and d.get("persona") == "coder"
              and d.get("model") == "mock2" and d.get("mode") == "execute",
              {k: (d or {}).get(k) for k in ("persona", "model", "mode")})

        print("== 5. 中止与删除隔离 ==")
        eng.stop(sidA)
        check("stop 只停指定任务",
              ctxA["cancel"].is_set() and not ctxB["cancel"].is_set())
        check("不能删除当前任务", "error" in eng.delete_session(sidB))
        _del = eng.delete_session(sidA)
        check("可以删除非激活且空闲的任务", "error" not in _del, _del)

        print("== 6. 运行中切换会话: 后台任务继续跑完 ==")
        _o2 = E.L.stream_chat

        def slow_stream(profile, active, msgs, tools, cancel=None,
                        on_reason=None, on_content=None):
            last = [m for m in msgs if m.get("role") == "user"][-1]["content"]
            n = 6 if "任务C" in last else 1
            for i in range(n):
                if on_content:
                    on_content("C进度%d/%d" % (i, n))
                time.sleep(0.10)
            return ({"role": "assistant", "content": "C完成", "tool_calls": None}, 0, None)

        E.L.stream_chat = slow_stream
        sidC = eng.new_session()["sid"]
        cC = eng._ctxs[sidC]
        check("任务C已建立", eng.state()["sid"] == sidC)
        check("C发送成功", eng.send("任务C: 长任务").get("ok") is not False)
        time.sleep(0.12)
        n_before = len([e for e in events if e.get("sid") == sidC])
        check("C正在运行", cC["busy"] is True)
        check("切换到另一个对话框(已存在的任务)",
              eng.open_session(sidB).get("ok") is True)
        check("引擎激活任务已切走", eng.state()["sid"] == sidB)
        t0 = time.time()
        while time.time() - t0 < 5 and cC["busy"]:
            time.sleep(0.04)
        n_after = len([e for e in events if e.get("sid") == sidC])
        check("切走后C继续产生事件(没有被取消)", n_after > n_before,
              (n_before, n_after))
        check("C最终跑完且状态复位", cC["busy"] is False)
        check("C写出自己的答案",
              any(m.get("content") == "C完成" for m in cC["sess"]["messages"]
                  if m.get("role") == "assistant"))
        c_ev = [e for e in events if e.get("sid") == sidC]
        check("C的事件全程只带C的sid(不串到B)",
              bool(c_ev) and all(e["sid"] == sidC for e in c_ev))
        check("后台结束仍发出round_end(前端据此收尾)",
              any(e.get("t") == "round_end" for e in c_ev))
        _by = {x["id"]: x for x in eng.list_sessions()}
        check("切走期间完成的任务带unread(列表可见)",
              _by.get(sidC, {}).get("unread") is True, _by.get(sidC))
        eng.open_session(sidC)
        check("切回C后unread清除",
              {x["id"]: x for x in eng.list_sessions()}.get(sidC, {}).get("unread") is False)
        E.L.stream_chat = _o2

        print("== 7. 本会话总是允许 按任务隔离 ==")
        import tools.permissions as _pp
        _pp.load_rules({})
        cB = eng._ctxs[sidB]
        with eng._bound(cC):
            _pp.grant_session("shell", "echo *")
            check("C授权后在C内直接放行", _pp.check("shell", "echo hi") == "allow")
        with eng._bound(cB):
            check("B不继承C的授权(仍逐次确认)",
                  _pp.check("shell", "echo hi") == "ask")
        _pp.load_rules({})

        print("\n多任务并行诊断: %d通过 / %d失败" % (len(PASS), len(FAIL)))
        return 1 if FAIL else 0
    finally:
        E.L.stream_chat, E.L.oneshot = _orig_stream, _orig_oneshot


if __name__ == "__main__":
    sys.exit(main())
