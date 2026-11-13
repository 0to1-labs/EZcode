#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SecOps Agent - 研判引擎 + CLI界面 (v0.6)
UI与引擎解耦: stream_llm/run_agent通过ui对象发事件:
  stream_start / reason_delta / reason_done / content_start / content_delta
  / content_end / tool_start / tool_running(ctx) / tool_done / round_done
- CLI: python -X utf8 agent.py   (回车=发送, 多行粘贴整块接收)
- TUI: python -X utf8 tui.py     (全屏OpenCode风格, Enter发送/Shift+Enter换行)
"""
import json
import os
import sys
import time
import contextlib
import datetime
import threading
import concurrent.futures

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown
    from rich.live import Live
    from rich.text import Text
    from rich.rule import Rule
    from rich.table import Table
    from rich.spinner import Spinner
except ImportError:
    print("缺少rich库, 请先执行: pip install rich")
    sys.exit(1)

import requests

import verify
import plugin_loader

from tools import TOOLS, HANDLERS, EXEC_TOOL_NAMES, configure, toolset

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
MAX_TOOL_ROUNDS = 40          # 单轮对话内LLM往返上限(防失控; 批量分诊需多次拉取/下钻, 24不够用)
TOOL_RESULT_LIMIT = 24000     # 单个工具结果进入上下文的字符上限
REASON_KEYS = ("reasoning_content", "reasoning", "thinking")  # 各家思考流字段名


class RoundCancelled(Exception):
    """用户中止本轮(双击ESC)"""


CONTEXT_CHAR_BUDGET = 150000     # 上下文字符预算(超了自动压缩历史工具结果)
COMPACTED_MARK = "...[已压缩:历史工具结果]"


def _compact_messages(messages, force=False):
    """上下文分页(课程对齐P14.07): 超预算时从旧到新压缩工具结果, 确定性, 保最近轮完整。
    force=True时无条件执行一轮压缩(/compact命令用)。"""
    def _total():
        return sum(len(str(m.get("content") or "")) for m in messages)
    if not force and _total() <= CONTEXT_CHAR_BUDGET:
        return False
    compacted = 0
    for m in messages:                      # 第一遍: 压缩旧工具结果
        if _total() <= CONTEXT_CHAR_BUDGET:
            break
        if m.get("role") == "tool" and COMPACTED_MARK not in (m.get("content") or "") \
                and len(m.get("content") or "") > 600:
            m["content"] = m["content"][:400] + " " + COMPACTED_MARK
            compacted += 1
    for m in messages:                      # 第二遍: 压缩超长的历史assistant回答
        if _total() <= CONTEXT_CHAR_BUDGET:
            break
        if m.get("role") == "assistant" and len(m.get("content") or "") > 4000:
            m["content"] = m["content"][:800] + " ...[已压缩:历史回答]"
            compacted += 1
    return compacted > 0


def repair_messages(messages):
    """中止后清理残缺消息: 移除悬挂的tool结果和无tool响应的assistant(tool_calls),
    避免下一轮请求因消息结构非法报400"""
    while messages and messages[-1].get("role") == "tool":
        messages.pop()
    if (messages and messages[-1].get("role") == "assistant"
            and messages[-1].get("tool_calls")):
        messages.pop()


# ---------------- 配置 ----------------

def load_config():
    """加载接入配置。固定文档名 devices.json 优先; 兼容旧 config.json。
    所有安全设备凭据集中在这一个文档里, 代码零凭据(开源友好)。"""
    for name in ("devices.json", "config.json"):
        path = os.path.join(BASE_DIR, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            cfg = _normalize_config(raw)
            cfg["_source"] = name
            return cfg
    print("缺少接入配置文档 devices.json")
    print("请先执行: Copy-Item devices.example.json devices.json  然后填入你的设备凭据")
    sys.exit(1)


def _normalize_config(raw):
    """把 devices.json / config.json 两种结构归一化成工具层所需的扁平键。"""
    cfg = dict(raw)
    # ---- 告警平台: ngsoc.sites(内联) 或 ngsoc.sites_file(外部文件) ----
    ngsoc = raw.get("ngsoc") or {}
    if isinstance(ngsoc, dict):
        if isinstance(ngsoc.get("sites"), list):
            cfg["ngsoc_sites"] = ngsoc["sites"]
        if ngsoc.get("sites_file"):
            cfg["ngsoc_sites_path"] = ngsoc["sites_file"]
        if ngsoc.get("verify_ssl") is not None:
            cfg["ngsoc_verify_ssl"] = bool(ngsoc["verify_ssl"])
    # 兼容 config.json 的旧键 ngsoc_sites_path
    # ---- 资产库 ----
    nb = raw.get("netbox") or {}
    if isinstance(nb, dict):
        if nb.get("base_url"):
            cfg["netbox_base"] = nb["base_url"]
        if nb.get("token"):
            cfg["netbox_token"] = nb["token"]
        if nb.get("verify_ssl") is not None:
            cfg["netbox_verify_ssl"] = bool(nb["verify_ssl"])
    # ---- 主机侧 ----
    edr = raw.get("edr") or {}
    if isinstance(edr, dict):
        if isinstance(edr.get("projects"), list):
            cfg["edr_projects"] = {p["name"]: (p["server"], p["token"])
                                   for p in edr["projects"] if p.get("name")}
        if edr.get("client_module_path"):
            cfg["edr_client_path"] = edr["client_module_path"]
    return cfg


def normalize_llm(cfg):
    llm = cfg.get("llm")
    if llm and llm.get("profiles"):
        profiles = {k: v for k, v in llm["profiles"].items() if isinstance(v, dict)}
        active = llm.get("active")
        if active not in profiles:
            active = next(iter(profiles))
        return profiles, active
    profiles = {"deepseek": {
        "base_url": cfg.get("deepseek_base_url", "https://api.deepseek.com"),
        "api_key": cfg.get("deepseek_api_key", ""),
        "model": cfg.get("model", "deepseek-chat"),
    }}
    return profiles, "deepseek"


def _check_key(profile, name):
    key = (profile.get("api_key") or "").strip()
    if key and ("填" in key or "你的" in key):
        raise RuntimeError("profile %r 的 api_key 还是占位符, 请在 devices.json 填真实key" % name)
    return key


def build_system_prompt(cfg):
    with open(os.path.join(BASE_DIR, "prompts", "system.md"), encoding="utf-8") as f:
        prompt = f.read()
    lines = ["", "# 环境信息(动态注入)", "当前时间: %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")]
    sites = []
    path = cfg.get("ngsoc_sites_path")
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                sites = json.load(f).get("sites", [])
        except Exception:
            pass
    if not sites and isinstance(cfg.get("ngsoc_sites"), list):
        sites = cfg["ngsoc_sites"]     # 内联站点(devices.json ngsoc.sites)
    if sites:
        lines.append("告警平台站点(ngsoc_search_alarms可用): " + ", ".join(
            "%s(%s,%s)" % (s["name"], s.get("code"), "新API" if s.get("apiStyle") == "new" else "旧API")
            for s in sites) + ", all=全站检索")
    else:
        lines.append("告警平台: 未配置站点(devices.json ngsoc.sites或sites_file), ngsoc工具不可用")
    lines.append("威胁情报: 公开多源查询(ThreatFox + ipwho.is 归属地), 内网IP自动跳过")
    lines.append("主机侧项目(edr_query可用): %s (以devices.json edr.projects登记为准)"
                 % (", ".join((cfg.get("edr_projects") or {}).keys()) or "未登记"))
    # 插件/MCP扩展
    st = plugin_loader.status()
    if st["plugin_tools"] or st["mcp_tools"]:
        lines.append("扩展工具已加载: 插件%s个(%s工具), MCP服务器%s个(%s工具)——"
                     "工具清单见本轮tools, 名字带mcp__前缀的是MCP工具"
                     % (len(st["plugins"]), st["plugin_tools"],
                        len(st["mcp_servers"]), st["mcp_tools"]))
    addon = plugin_loader.prompt_addon()
    if addon:
        lines.append(addon)
    return "\n".join([prompt] + lines)


# ---------------- 会话日志 ----------------

class SessionLog:
    """全留痕: 思考/工具调用/结果/结论, 作研判证据链复盘用(并行安全)。
    path参数可复用旧日志文件(会话切回场景)。
    延迟建文件: 首次写入才open——启动后不发消息不留空日志。"""

    def __init__(self, path=None):
        os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
        self._fh = None
        if path:
            self.path = path
        else:
            name = "session-%s.log" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            self.path = os.path.join(BASE_DIR, "logs", name)
        self._lock = threading.Lock()

    def _ensure_open(self):
        if self._fh is None:
            self._fh = open(self.path, "a", encoding="utf-8")

    def log(self, kind, text):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self._ensure_open()
            self._fh.write("\n===[%s %s]===\n%s\n" % (ts, kind, text))
            self._fh.flush()

    def close(self):
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None


# ---------------- CLI UI实现 ----------------

class CliUI:
    """CLI输出: 思考Live窗+工具状态行 (与v0.5行为一致)"""

    def __init__(self, console):
        self.console = console
        self._live = None
        self._phase = None
        self._t0 = 0.0

    # --- 生命周期 ---
    def stream_start(self):
        self._t0 = time.time()
        self._phase = None

    def _render_reason(self, buf):
        tail = buf[-420:]
        spin = Spinner("dots", text=Text(" 思考中 · %d字 · %.0fs" % (len(buf), time.time() - self._t0),
                                          style="cyan"))
        from rich.console import Group
        return Group(spin, Text("  " + tail, style="dim italic"))

    def reason_delta(self, buf):
        if self._phase != "reason":
            if self._live:
                self._live.stop()
            self._live = Live(self._render_reason(buf), console=self.console,
                              refresh_per_second=10, transient=True)
            self._live.start()
            self._phase = "reason"
        else:
            self._live.update(self._render_reason(buf))

    def reason_done(self, total, elapsed):
        if self._phase == "reason":
            if self._live:
                self._live.stop()
                self._live = None
            if total:
                self.console.print(Text("  ◦ 思考完成 · %d字 · %.1fs" % (total, elapsed), style="dim"))
            self._phase = None

    def content_start(self):
        self._live = Live(Markdown(""), console=self.console, refresh_per_second=4)
        self._live.start()
        self._phase = "content"

    def content_delta(self, buf):
        self._live.update(Markdown(buf))

    def content_end(self, buf):
        if self._live:
            self._live.stop()
            self._live = None
        self.console.print()

    def tool_start(self, fname, args_str):
        self.console.print("  [bold cyan]⚙ %s[/] [dim]%s[/]" % (fname, args_str[:120]))

    @contextlib.contextmanager
    def tool_running(self, fname):
        with self.console.status("[dim]执行 %s ...[/]" % fname, spinner="dots"):
            yield

    def sub_progress(self, text):
        """子代理进度心跳: 单行低频打印(rich console线程安全)"""
        self.console.print("  [dim]· %s[/]" % text)

    def tool_done(self, fname, ok, dt, size, err=None):
        if ok:
            self.console.print("  [green]└ ✓ %s[/] [dim]· %.1fs · 结果%s字符[/]" % (fname, dt, format(size, ",")))
        else:
            self.console.print("  [red]└ ✗ %s · %.1fs · %s[/]" % (fname, dt, str(err)[:150]))

    def round_done(self, answer, dt, n_tools, n_think, slog):
        pass  # CLI在main循环里打印统计Rule


# ---------------- 流式LLM(引擎, 无UI依赖) ----------------

def _req_body(profile, messages, tools_list, stream):
    body = {"model": profile.get("model", "deepseek-chat"),
            "messages": messages, "tools": tools_list}
    temp = profile.get("temperature")
    if temp is not None:
        body["temperature"] = temp
    # 输出上限(OpenCode limit.output同款): 服务端封顶思考+正文总量, 防失控生成
    out_limit = profile.get("output")
    if out_limit:
        body["max_tokens"] = int(out_limit)
    if stream:
        body["stream"] = True
    return body


def _post(profile, profile_name, body, stream=True):
    url = profile["base_url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    headers = {"Content-Type": "application/json"}
    key = _check_key(profile, profile_name)
    if key:
        headers["Authorization"] = "Bearer " + key
    return requests.post(url, headers=headers, json=body, stream=stream,
                         timeout=(15, 60 if stream else 300))
                         # 流式: 每chunk 60s读超时(停滞快速报错); 非流式: 整体生成上限300s


def stream_llm(profile, profile_name, messages, slog, ui, tools_list=None, cancel=None):
    """流式调用。返回 assistant_message dict, 经ui回调实时渲染。cancel=中止事件。"""
    tools_list = TOOLS if tools_list is None else tools_list
    t0 = time.time()
    ui.stream_start()
    body = _req_body(profile, messages, tools_list, stream=True)
    try:
        resp = _post(profile, profile_name, body, stream=True)
    except requests.RequestException as e:
        raise RuntimeError("连接LLM失败: %s" % str(e)[:200])
    if resp.status_code != 200:
        txt = resp.text[:300]
        if "stream" in txt.lower():
            return _nonstream_llm(profile, profile_name, messages, txt, ui, tools_list)
        raise RuntimeError("LLM[%s] %s: %s" % (profile_name, resp.status_code, txt))

    reasoning = []
    content = []
    tc_agg = {}
    phase = None

    try:
        for raw in resp.iter_lines(chunk_size=None):
            if cancel is not None and cancel.is_set():
                raise RoundCancelled()
            if not raw:
                continue
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                j = json.loads(data)
            except Exception:
                continue
            ch = (j.get("choices") or [{}])[0]
            delta = ch.get("delta") or {}
            for rk in REASON_KEYS:
                if delta.get(rk):
                    reasoning.append(delta[rk])
                    ui.reason_delta("".join(reasoning))
                    break
            if delta.get("content"):
                if phase == "reason" or (phase is None and reasoning):
                    ui.reason_done(len("".join(reasoning)), time.time() - t0)
                if phase != "content":
                    ui.content_start()
                    phase = "content"
                content.append(delta["content"])
                ui.content_delta("".join(content))
            if delta.get("tool_calls"):
                for tc in delta["tool_calls"]:
                    i = tc.get("index", 0)
                    a = tc_agg.setdefault(i, {"id": "", "name": "", "args": ""})
                    if tc.get("id"):
                        a["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        a["name"] = fn["name"]
                    if fn.get("arguments"):
                        a["args"] += fn["arguments"]
    except KeyboardInterrupt:
        raise
    finally:
        try:
            resp.close()
        except Exception:
            pass

    if phase == "reason" and not tc_agg:
        ui.reason_done(len("".join(reasoning)), time.time() - t0)
    if phase == "content":
        ui.content_end("".join(content))
    if reasoning:
        slog.log("思考", "".join(reasoning))

    tool_calls = None
    if tc_agg:
        tool_calls = [{"id": v["id"] or ("call_%d" % i), "type": "function",
                       "function": {"name": v["name"], "arguments": v["args"]}}
                      for i, v in sorted(tc_agg.items())]
    msg = {"role": "assistant", "content": "".join(content) if content else None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg, len("".join(reasoning)), time.time() - t0


def _nonstream_llm(profile, profile_name, messages, errtxt, ui, tools_list):
    ui.reason_done(0, 0)
    t0 = time.time()
    body = _req_body(profile, messages, tools_list, stream=False)
    with ui.tool_running("LLM请求(非流式)"):
        resp = _post(profile, profile_name, body, stream=False)
    if resp.status_code != 200:
        raise RuntimeError("LLM[%s] %s: %s" % (profile_name, resp.status_code, resp.text[:300]))
    msg = resp.json()["choices"][0]["message"]
    full = msg.get("content") or ""
    ui.content_start()
    ui.content_delta(full)
    ui.content_end(full)
    return msg, 0, time.time() - t0


# ---------------- Agent主循环(引擎) ----------------

def _execute_tool(tc, allow_exec, slog, cancel, ui=None):
    """执行单个工具调用, 返回(result dict, dt)。不做UI完成行(收尾统一在循环里按序做)。
    ui=None时为并行线程内执行(无spinner)。"""
    fname = tc["function"]["name"]
    args_str = tc["function"].get("arguments") or "{}"
    t0 = time.time()
    if cancel is not None and cancel.is_set():
        raise RoundCancelled()
    if fname in EXEC_TOOL_NAMES and not allow_exec:
        return ({"error": "当前为只读查询模式, 处置类操作被拒绝。"
                         "请告知用户: 如需执行处置, 按Tab切换到可执行模式后重试"},
                time.time() - t0)
    try:
        args = json.loads(args_str or "{}")
    except Exception as e:
        return {"error": "参数解析失败: %s" % e}, time.time() - t0
    if fname == "subagent":
        try:
            result = dispatch_subagent(args.get("task", ""), args.get("agent", "explore"),
                                       cancel=cancel, slog=slog, main_ui=ui)
        except RoundCancelled:
            raise
        except Exception as e:
            result = {"error": "子代理执行失败: %s: %s" % (type(e).__name__, e)}
        return result, time.time() - t0
    handler = HANDLERS.get(fname)
    if handler is None:
        return {"error": "未知工具 %r" % fname}, time.time() - t0

    def _run():
        if ui is None:
            return handler(**args)
        try:
            ctx = ui.tool_running(fname)
        except Exception:
            ctx = None
        if ctx is not None and hasattr(ctx, "__enter__"):
            with ctx:
                return handler(**args)
        return handler(**args)

    try:
        result = _run()
    except TypeError as e:
        result = {"error": "参数不匹配: %s" % e}
    except RoundCancelled:
        raise
    except Exception as e:
        result = {"error": "%s: %s" % (type(e).__name__, e)}
    if not isinstance(result, dict):
        result = {"result": result}
    return result, time.time() - t0


def run_agent(profile, profile_name, messages, slog, ui, tools_list=None, allow_exec=False,
              cancel=None, max_rounds=None, session_tool_log=None):
    """Agent主循环。tools_list=当前模式可用工具; allow_exec=是否允许处置类工具; cancel=中止事件;
    max_rounds=步数上限(子代理用, 末步自动移除工具强制总结, 对齐OpenCode steps语义);
    session_tool_log=会话级工具台账(list), 传入后本轮调用会同步累积进去,
    验证门可看到前几轮的证据记录(修复跨轮结论被误block的问题)。"""
    tools_list = TOOLS if tools_list is None else tools_list
    RUNTIME["profile"], RUNTIME["name"] = profile, profile_name   # 子代理调度器读取当前模型
    rounds = int(max_rounds) if max_rounds else MAX_TOOL_ROUNDS
    total_think = 0
    tool_count = 0
    tool_log = []          # 工具调用台账(供确定性校验门重算)
    t_start = time.time()
    try:
        for _round in range(rounds):
            if cancel is not None and cancel.is_set():
                raise RoundCancelled()
            if _compact_messages(messages):
                slog.log("系统", "上下文超预算, 已确定性压缩历史工具结果(P14.07分页)")
            # 末步移除工具, 强制文本总结(OpenCode steps语义)
            round_tools = tools_list
            if max_rounds and _round >= rounds - 1:
                round_tools = []
            msg, rlen, elapsed = stream_llm(profile, profile_name, messages, slog, ui,
                                            round_tools, cancel)
            total_think += rlen
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                content = msg.get("content") or ""
                if not content.strip():
                    # 模型空响应(罕见): 给出可操作提示
                    content = ("(模型本轮输出为空。建议: 重新发送该问题, 或换模型, 或简化输入)")
                # ---- 确定性验证门(P14.38): 从工具台账重算, 不采信模型自评 ----
                # 会话级台账+本轮台账合并: 跨轮引用前轮证据(如前轮已查情报)不再误block
                verify_log = tool_log if session_tool_log is None \
                    else (list(session_tool_log[-50:]) + tool_log)
                gate = verify.verify_round(verify_log, messages, content)
                if gate["findings"]:
                    # 校验结果由 ui.gate_report 单独呈现并留痕; 不拼进回答正文,
                    # 避免系统旁注进入对话历史, 诱导模型下一轮围绕"封禁/未通过校验"纠缠。
                    slog.log("校验", "passed=%s\n%s\n%s" % (
                        gate["passed"],
                        "\n".join(f["code"] + ": " + f["text"] for f in gate["findings"]),
                        verify.gate_banner(gate)))
                    try:
                        ui.gate_report(gate)
                    except Exception:
                        pass
                messages.append({"role": "assistant", "content": content})
                slog.log("结论", content)
                ui.round_done(content, time.time() - t_start, tool_count, total_think, slog)
                return content, tool_count, total_think, time.time() - t_start
            messages.append({"role": "assistant",
                             "content": msg.get("content"),
                             "tool_calls": tool_calls})
            for tc in tool_calls:
                ui.tool_start(tc["function"]["name"],
                              tc["function"].get("arguments") or "{}")
            # ---- 执行: 同一批的多个subagent调用并行(线程池, evidence按站点限频互不冲突), 其余顺序 ----
            # 并行分支也传主ui: subagent不走tool_running spinner, 只经SubUI.sub_progress推状态行(线程安全)
            results = {}
            sub_positions = [i for i, tc in enumerate(tool_calls)
                             if tc["function"]["name"] == "subagent"]
            if len(sub_positions) >= 2:
                with concurrent.futures.ThreadPoolExecutor(
                        max_workers=min(4, len(sub_positions))) as ex:
                    futs = {i: ex.submit(_execute_tool, tool_calls[i], allow_exec, slog, cancel, ui)
                            for i in sub_positions}
                for i in sub_positions:
                    results[i] = futs[i].result()
            for i, tc in enumerate(tool_calls):
                if i not in results:
                    if cancel is not None and cancel.is_set():
                        raise RoundCancelled()
                    results[i] = _execute_tool(tc, allow_exec, slog, cancel, ui)
            # ---- 收尾(保持调用顺序): 完成行/日志/消息 ----
            for i, tc in enumerate(tool_calls):
                fname = tc["function"]["name"]
                args_str = tc["function"].get("arguments") or "{}"
                result, dt = results[i]
                tool_count += 1
                ok = "error" not in result
                slog.log("工具", "%s(%s)\n-> %s" % (fname, args_str,
                                                    json.dumps(result, ensure_ascii=False, default=str)[:8000]))
                content = json.dumps(result, ensure_ascii=False, default=str)
                if len(content) > TOOL_RESULT_LIMIT:
                    content = (content[:TOOL_RESULT_LIMIT]
                               + '\n...[结果被引擎截断: 请改用分页/检索参数(如 doc_read 的 '
                                 'offset/find)缩小范围; 不要用更大的 max_chars 重读同一段]')
                tool_log.append({"name": fname, "args": args_str, "result_head": content[:2000]})
                if session_tool_log is not None:
                    session_tool_log.append(tool_log[-1])   # 同步进会话级台账
                # 先落消息再通知UI: GuiUI.tool_done 从messages取本次结果做证据预览, 顺序不能反
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": content})
                ui.tool_done(fname, ok, dt,
                             len(json.dumps(result, ensure_ascii=False, default=str)),
                             result.get("error"))
    except RoundCancelled:
        repair_messages(messages)
        slog.log("系统", "用户中止本轮(ESC双击), 已清理残缺消息")
        ui.round_done("", time.time() - t_start, tool_count, total_think, slog)
        raise
    return ("(已达单轮工具调用上限, 中止本轮。请缩小问题范围或 /new 后继续)",
            tool_count, total_think, time.time() - t_start)


def llm_ask(profile, profile_name, messages, max_chars=6000, tools=None):
    """一次性LLM问答(隐藏agent与/btw用, 可带上下文)。非流式, 返回文本。"""
    temp = profile.get("temperature")
    if temp is None:
        temp = 0.2
    body = {"model": profile.get("model", "deepseek-chat"), "temperature": temp,
            "messages": messages}
    out_limit = profile.get("output")
    if out_limit:
        body["max_tokens"] = int(out_limit)
    if tools:
        body["tools"] = tools
    resp = _post(profile, profile_name, body, stream=False)
    if resp.status_code != 200:
        raise RuntimeError("LLM %s: %s" % (resp.status_code, resp.text[:300]))
    msg = resp.json()["choices"][0]["message"]
    return (msg.get("content") or "")[:max_chars]


# ---------------- 会话命令共享逻辑(OpenCode对齐: @file/!shell/undo/export/compact) ----------------

_AT_FILE_RE = None


def expand_at_files(text, max_chars=30000):
    """@路径 文件附件展开(OpenCode @file语义): 内容嵌入提示词。
    安全: ①@explore/@general/@rca等子代理点名委派不当作附件 ②读前过file_read权限规则(敏感路径deny)"""
    import re
    from tools.permissions import gate as _perm_gate

    def _repl(m):
        raw = m.group(1)
        if raw.lower() in {k.lower() for k in SUBAGENTS}:
            return m.group(0)   # 子代理点名委派(@explore任务/@general任务/@rca任务), 保持原样
        path = os.path.expandvars(os.path.expanduser(raw))
        reason, ok = _perm_gate("file_read", path, "附件读取: %s" % path)
        if not ok:
            return "[附件 %s 读取被拒绝: %s]" % (raw, reason)
        try:
            content = open(path, encoding="utf-8", errors="replace").read()
        except Exception as e:
            return "[附件 %s 读取失败: %s]" % (raw, e)
        if len(content) > max_chars:
            content = content[:max_chars] + " ...[附件过大已截断]"
        return "[附件 %s 内容如下]\n```\n%s\n```" % (raw, content)

    return re.sub(r"(?<![\w@])@([^\s@,;，；]+)", _repl, text)


def run_shell(cmd, timeout=60):
    """!命令 shell模式(OpenCode !语义): 委托权限模块执行"""
    from tools.permissions import run_shell_cmd
    return run_shell_cmd(cmd, timeout)


def export_session(messages, path=None):
    """会话导出为markdown(OpenCode /export语义)。含工具调用与结果(证据链复盘用)。"""
    if not path:
        path = os.path.join(BASE_DIR, "logs", "export-%s.md"
                            % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    lines = ["# EZcode 会话导出", ""]
    for m in messages:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "user":
            lines.append("## 你\n\n%s\n" % content)
        elif role == "assistant":
            tcs = m.get("tool_calls") or []
            for tc in tcs:
                fn = (tc.get("function") or {})
                lines.append("> ⚙ **%s**(%s)\n" % (fn.get("name"), (fn.get("arguments") or "")[:300]))
            if content:
                lines.append("## EZcode\n\n%s\n" % content)
        elif role == "tool":
            lines.append("<details><summary>⚙ 工具结果</summary>\n\n```\n%s\n```\n\n</details>\n"
                         % content[:2500])
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ---------------- 子代理架构 (对齐OpenCode V2: fresh context + steps + 只读委派) ----------------

RUNTIME = {"cfg": None, "profiles": None, "profile": None, "name": None}


def set_runtime(cfg, profiles):
    RUNTIME["cfg"] = cfg
    RUNTIME["profiles"] = profiles
    if not RUNTIME.get("profile") and profiles:
        first = next(iter(profiles))
        RUNTIME["profile"], RUNTIME["name"] = profiles[first], first


def llm_list_models(profile, profile_name):
    """发现provider可用模型(GET /v1/models, OpenCode /models选择器同款数据源)"""
    url = profile["base_url"].rstrip("/")
    if not url.endswith("/models"):
        url += "/models"
    headers = {}
    try:
        key = _check_key(profile, profile_name)
    except RuntimeError:
        key = ""
    if key:
        headers["Authorization"] = "Bearer " + key
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code != 200:
        raise RuntimeError("HTTP %s: %s" % (r.status_code, r.text[:120]))
    j = r.json()
    return [m.get("id") for m in (j.get("data") or []) if m.get("id")]


def llm_oneshot(profile, profile_name, system, user, max_chars=4000):
    """一次性LLM调用(隐藏系统agent用: title/summary)。非流式。"""
    temp = profile.get("temperature")
    if temp is None:
        temp = 0.2
    body = {"model": profile.get("model", "deepseek-chat"), "temperature": temp,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    out_limit = profile.get("output")
    if out_limit:
        body["max_tokens"] = int(out_limit)
    resp = _post(profile, profile_name, body, stream=False)
    if resp.status_code != 200:
        raise RuntimeError("LLM %s: %s" % (resp.status_code, resp.text[:200]))
    msg = resp.json()["choices"][0]["message"]
    return (msg.get("content") or "")[:max_chars]


SUBAGENTS = {
    "explore": {
        "description": "只读侦察子代理: 批量告警快速分诊/行为链普查/多站点扫查, 输出结构化简报",
        "steps": 8,
        "prompt_suffix": (
            "\n# 角色追加: 你是只读侦察子代理(explore), 任务来自主代理委派, 在全新上下文中工作\n"
            "- 快速调查并输出结构化简报: 概览(N条/严重度分布) / 每条一行快速定性标签 / 需主代理深挖的可疑点\n"
            "- 已知误报族引用判据编号快速定性; 低风险扫描合并带过\n"
            "- 不下最终定性结论, 不给处置建议, 不派生其他子代理\n"),
    },
    "general": {
        "description": "深度调查子代理: 单一主题多步研究(资产/行为链/报文取证/情报), 输出完整证据报告",
        "steps": 16,
        "prompt_suffix": (
            "\n# 角色追加: 你是深度调查子代理(general), 任务来自主代理委派, 在全新上下文中工作\n"
            "- 围绕单一主题完整取证, 输出带证据分级([实证]/[推断]/[待确认])的报告\n"
            "- 遵守全部研判纪律: 报文级铁律/证据分级/情报必查/防火墙Action为准\n"
            "- 只读: 不执行任何写操作, 不派生其他子代理\n"),
    },
}


def _load_custom_agents():
    """agents/*.md 自定义子代理(frontmatter: description/mode/steps, 正文=角色提示词追加)"""
    import re
    agent_dir = os.path.join(BASE_DIR, "agents")
    if not os.path.isdir(agent_dir):
        return
    for fn in sorted(os.listdir(agent_dir)):
        if not fn.endswith(".md"):
            continue
        name = fn[:-3]
        try:
            text = open(os.path.join(agent_dir, fn), encoding="utf-8").read()
            meta, body = {}, text
            m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
            if m:
                for line in m.group(1).splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip().strip('"').strip("'")
                body = m.group(2)
            steps = max(2, int(meta.get("steps") or 8))
            mode = (meta.get("mode") or "subagent").lower()
            if mode not in ("subagent", "all"):
                continue
            SUBAGENTS[name] = {
                "description": meta.get("description") or ("自定义子代理 " + name),
                "steps": steps,
                "prompt_suffix": "\n# 自定义角色: %s\n%s\n" % (name, body.strip()[:3000]),
            }
        except Exception:
            continue


_load_custom_agents()


def _subagent_catalog():
    return "; ".join("%s: %s (steps≤%d)" % (k, v["description"][:44], v["steps"])
                     for k, v in SUBAGENTS.items())


SUBAGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "subagent",
        "description": "委派子代理在全新上下文中执行任务(主上下文不被污染, 对齐OpenCode子代理架构)。"
                       "任务必须自包含(写清站点/时间/IP/告警要素)。子代理返回结构化简报/报告。"
                       "**并行**: 多个独立调查任务请在本批tool_calls里同时发起多个subagent调用——"
                       "引擎自动并行执行(≤4并发); evidence限频按告警平台站点独立计时, 不同站点互不冲突。"
                       "可用子代理: "
                       + _subagent_catalog(),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "自包含的任务描述(含全部要素与期望输出格式)"},
                "agent": {"type": "string", "enum": sorted(SUBAGENTS.keys()),
                          "description": "子代理名: 批量分诊侦察用explore, 单主题深度调查用general"},
            },
            "required": ["task", "agent"],
        },
    },
}


class _SubUI:
    """子代理静默UI: 思考/结果不刷主界面(只落日志), 但把关键进度经sub_progress推给主UI状态行,
    避免子代理长跑(数分钟)时界面零反馈被误以为卡死。main_ui=None时退回全静默。
    sub_progress各实现均线程安全(并行子代理场景): CliUI直接print/GuiUI有锁队列/TuiUI走call_from_thread。"""

    def __init__(self, main_ui=None, agent_name="sub", steps=8):
        self.main = main_ui
        self.name = agent_name
        self.steps = steps
        self.round = 0
        self._last_push = 0.0
        self._tag = "子代理%s" % agent_name

    def _progress(self, text):
        if self.main is None:
            return
        try:
            self.main.sub_progress("%s · %s" % (self._tag, text))
        except Exception:
            pass

    def stream_start(self):
        self.round += 1
        self._last_push = time.time()
        self._progress("第%d/%d步 思考中…" % (self.round, self.steps))

    def reason_delta(self, buf):
        now = time.time()
        if now - self._last_push >= 5.0:    # 5秒节流, 思考再长也有心跳
            self._last_push = now
            self._progress("第%d/%d步 思考中(%d字)" % (self.round, self.steps, len(buf)))

    def reason_done(self, total, elapsed):
        self._progress("第%d/%d步 思考完成(%d字, %.0fs)" % (self.round, self.steps, total, elapsed))

    def tool_start(self, fname, args_str):
        self._progress("第%d/%d步 调用工具 %s" % (self.round, self.steps, fname))

    @contextlib.contextmanager
    def tool_running(self, fname):
        self._progress("第%d/%d步 工具运行中: %s" % (self.round, self.steps, fname))
        yield

    def __getattr__(self, k):
        def noop(*a, **kw):
            pass
        return noop


def dispatch_subagent(task, agent="explore", cancel=None, slog=None, main_ui=None):
    spec = SUBAGENTS.get(agent)
    if not spec:
        return {"error": "未知子代理 %r, 可用: %s" % (agent, ", ".join(sorted(SUBAGENTS)))}
    cfg = RUNTIME.get("cfg")
    profile = RUNTIME.get("profile")
    name = RUNTIME.get("name")
    if not (cfg and profile):
        return {"error": "运行时未初始化"}
    sub_messages = [
        {"role": "system", "content": build_system_prompt(cfg) + spec["prompt_suffix"]},
        {"role": "user", "content": str(task)[:8000]},
    ]
    sub_tools = [t for t in toolset("query") if t["function"]["name"] != "subagent"]
    # 子代理日志并入主会话日志(不再一dispatch一文件, 避免logs膨胀); 无主日志时(独立调用)退回新文件
    own_log = slog is None
    slog = slog or SessionLog()
    slog.log("子代理开始", "agent=%s steps=%d\n任务: %s" % (agent, spec["steps"], str(task)[:500]))
    answer, n_tools, n_think, dt = run_agent(
        profile, name, sub_messages, slog, _SubUI(main_ui, agent, spec["steps"]),
        tools_list=sub_tools, allow_exec=False, max_rounds=spec["steps"], cancel=cancel)
    slog.log("子代理结束", "agent=%s 用时%.1fs 工具%d次" % (agent, dt, n_tools))
    if own_log:
        slog.close()
    truncated = len(answer) > 18000
    out = {"agent": agent, "report": answer[:18000], "tools_used": n_tools,
           "think_chars": n_think, "elapsed": "%.1fs" % dt,
           "log": os.path.basename(slog.path)}
    if truncated:
        out["report_truncated"] = True
        out["note"] = ("报告超长已截断——批量任务请拆小委派(如按站点/按告警批次), "
                       "或要求子代理只输出简表(每条一行定性)后由主agent对可疑项单独深挖")
    else:
        out["note"] = "子代理为只读且不可派生下级; 全过程留痕在上述日志(与主会话同文件, 见'子代理开始/结束'标记间)"
    return out


# 注册子代理工具进全局注册表(两种模式均可用, 委派本身是只读操作)
TOOLS.append(SUBAGENT_SCHEMA)
HANDLERS["subagent"] = dispatch_subagent


# ---------------- CLI交互 ----------------

HELP = """
[bold]用法[/]
  - 输入问题后[bold]直接回车发送[/]; 多行告警文本[bold]整块粘贴[/]即可(自动整块接收)
  - 推荐用全屏TUI: python -X utf8 tui.py (Enter发送, Shift/Ctrl+Enter换行)

[bold]命令[/]
  /model           查看当前模型与可用profile
  /model <名字>    切换LLM (%s)
  /new             开始新会话(清空上下文, 研判新告警前建议先/new)
  /help            本帮助
  /exit            退出

[bold]提示[/]
  - 左下角转圈 = 模型思考中; ⚙ 行 = 工具调用中
  - evidence报文下钻限频约12秒/次属正常
  - 全部内容留痕在 logs/session-*.log, 可作研判证据链复盘
"""


def read_user_input():
    """回车=发送。多行粘贴靠'突发检测': 连续读空为止。"""
    print("╭─ 你 (输入后回车发送; 多行告警直接整块粘贴)")
    try:
        first = input()
    except EOFError:
        print("╰─")
        return ""
    lines = [first]
    try:
        import msvcrt
        while True:
            deadline = time.time() + 0.25
            more = False
            while time.time() < deadline:
                if msvcrt.kbhit():
                    more = True
                    break
                time.sleep(0.01)
            if not more:
                break
            try:
                lines.append(input())
            except EOFError:
                break
    except ImportError:
        pass
    print("╰─")
    return "\n".join(lines).rstrip("\n")


def main():
    console = Console()
    cfg = load_config()
    profiles, active = normalize_llm(cfg)
    configure(cfg)
    set_runtime(cfg, profiles)
    plugin_loader.bootstrap(cfg)
    for w in plugin_loader.warnings():
        console.print("[yellow][扩展加载] %s[/]" % w)
    from tools import permissions as perm
    from tools.permissions import set_confirm as _set_confirm

    def cli_confirm(action, resource, detail):
        console.print("[yellow]⚠ 权限确认[/]")
        console.print("[yellow]  %s[/]" % str(detail)[:300])
        try:
            ans = input("  允许执行? [y=这一次 / A=本会话总是 / n=拒绝]: ").strip().lower()
        except EOFError:
            return False
        if ans == "a":
            perm.grant_session(action, resource)
            return True
        return ans == "y"

    _set_confirm(cli_confirm)
    if not plugin_loader.is_mcp_done():
        console.print("[dim]MCP服务器后台连接中, 工具就绪后自动可用...[/]")

        def _on_mcp(st):
            if st["mcp_servers"]:
                console.print("[dim]MCP就绪: %s (注册%s个工具)[/]"
                              % (", ".join(st["mcp_servers"]), st["mcp_tools"]))
            for w in plugin_loader.warnings():
                if "连接失败" in w:
                    console.print("[yellow][MCP] %s[/]" % w)

        plugin_loader.set_on_mcp_done(_on_mcp)
    system_prompt = build_system_prompt(cfg)
    messages = [{"role": "system", "content": system_prompt}]
    slog = SessionLog()
    ui = CliUI(console)
    mode = "query"  # 只读查询(默认) / execute=可执行处置回写
    model_override = None   # /models 或 /model <profile> <model> 的模型覆盖
    model_index = []        # /models 发现的模型列表(提前初始化, /model <编号>未先/models也不再NameError)
    undo_stack = []         # /undo 撤回栈
    session_tool_log = []   # 会话级工具台账(验证门跨轮重算用)
    st = plugin_loader.status()
    from tools import ngsoc as _ngsoc
    n_sites = len(_ngsoc._sites)
    site_txt = ("%d个告警平台站点" % n_sites) if n_sites else "告警平台未配置"
    info = Table.grid(padding=(0, 2))
    info.add_row("[green]模型[/]", "%s [dim](%s)[/]" % (profiles[active].get("model"), profiles[active]["base_url"]),
                 "[green]站点[/]", site_txt, "[green]模式[/]", "[cyan]只读查询[/] (/mode切换)")
    info.add_row("[green]日志[/]", os.path.basename(slog.path), "[green]TUI[/]", "python tui.py (Tab切模式)",
                 "[green]扩展[/]", "插件%s/MCP%s" % (st["plugin_tools"], st["mcp_tools"]))
    console.print(Panel(info, title="[bold]EZcode[/] · CLI模式 · 推荐用 tui.py 全屏模式",
                        border_style="cyan"))
    while True:
        user = read_user_input()
        cmd = user.strip()
        if not cmd:
            continue
        if cmd in ("/exit", "/quit", "exit"):
            console.print("[dim]日志已留痕: %s[/]" % slog.path)
            slog.close()
            break
        if cmd == "/new":
            messages = [{"role": "system", "content": system_prompt}]
            slog = SessionLog()
            undo_stack = []          # 新会话不可恢复旧会话的消息
            session_tool_log = []    # 台账重置
            console.print("[dim]已开始新会话, 日志: %s[/]\n" % slog.path)
            continue
        if cmd == "/help":
            console.print(HELP % ", ".join(profiles))
            continue
        if cmd == "/summary":
            console.print("[dim]生成会话摘要中...[/]")
            try:
                convo = []
                for m in messages:
                    if m.get("role") in ("user", "assistant") and m.get("content"):
                        convo.append(("用户" if m["role"] == "user" else "研判") + ": "
                                     + str(m["content"])[:1200])
                text = llm_oneshot(profiles[active], active,
                                   "你是会话摘要器。总结这次安全运营会话: ①研判的告警与定性结论(带证据等级)"
                                   "②关键证据链 ③待办事项。300字内。",
                                   "\n\n".join(convo[-40:]), max_chars=2500)
                slog.log("会话摘要", text)
                console.print(Markdown(text))
            except Exception as e:
                console.print("[red]摘要失败: %s[/]" % e)
            continue
        if cmd == "/mode":
            mode = "execute" if mode == "query" else "query"
            if mode == "execute":
                messages.append({"role": "system", "content": "[模式切换] 可执行模式: 解锁ngsoc_dispose_alarms(告警平台告警处置回写)。执行前必须复述告警清单并获用户明确确认(confirmed=true), 误报定性需报文级证据"})
                console.print("[orange3]已切换到可执行模式: 处置回写解锁(仍需逐次确认)[/]")
            else:
                messages.append({"role": "system", "content": "[模式切换] 只读查询模式: 不执行任何处置回写, 只做查询研判"})
                console.print("[cyan]已切换到只读查询模式[/]")
            continue
        if cmd == "/models":
            console.print("[dim]发现可用模型中...[/]")
            model_index = []

            def _discover(pname, prof):
                try:
                    return pname, llm_list_models(prof, pname)
                except Exception:
                    return pname, [prof.get("model", "?")]

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(profiles))) as ex:
                for pname, models in ex.map(_discover, profiles.keys(), profiles.values()):
                    for mid in models:
                        model_index.append((pname, mid))
                        cur = (model_override or profiles[pname].get("model")) if pname == active else None
                        mark = " ←当前" if cur == mid else ""
                        console.print("  [%d] [bold]%s[/] (%s)%s" % (len(model_index) - 1, mid, pname, mark))
            console.print("[dim]切换: /model <编号>  或  /model <profile> <model_id>[/]")
            continue
        if cmd == "/model" or cmd.startswith("/model "):
            parts = cmd.split()
            if len(parts) == 1:
                ov = model_override or ""
                console.print("当前: [%s] %s (%s)%s" % (active, profiles[active].get("model"),
                                                        profiles[active]["base_url"],
                                                        (" (覆盖:%s)" % ov) if ov else ""))
                console.print("可用profile: " + ", ".join(
                    "[%s] %s" % (k, v.get("model")) for k, v in profiles.items()))
                console.print("切换: /models 列出全部 · /model <编号/profile> [model_id]")
            elif parts[1].isdigit() and model_index and int(parts[1]) < len(model_index):
                name, mid = model_index[int(parts[1])]
                active = name
                model_override = mid if mid != profiles[name].get("model") else None
                console.print("[cyan]已切换: %s (%s)[/]" % (mid, name))
            elif parts[1] in profiles:
                active = parts[1]
                model_override = parts[2] if len(parts) > 2 else None
                shown = model_override or profiles[active].get("model")
                console.print("[dim]已切换到 [%s] %s[/]" % (active, shown))
            else:
                console.print("[red]未知 %r, 可用: %s[/]" % (parts[1], ", ".join(profiles)))
            continue
        if cmd == "/status":
            console.print("[bold]运行状态[/]")
            eff_profile = dict(profiles[active])
            if model_override:
                eff_profile["model"] = model_override
            console.print("  生效模型: %s (%s) | 模式: %s" % (
                eff_profile["model"], active, "只读查询" if mode == "query" else "可执行"))
            console.print("  会话: %d条消息 · 约%s字符" % (
                len(messages), format(sum(len(str(m.get("content") or "")) for m in messages), ",")))
            st = plugin_loader.status()
            console.print("  扩展: 插件%s工具 · MCP%s服务器(%s工具) | 日志: %s" % (
                st["plugin_tools"], len(st["mcp_servers"]), st["mcp_tools"],
                os.path.basename(slog.path)))
            continue
        if cmd == "/compact":
            n = _compact_messages(messages, force=True)
            console.print("[cyan]上下文已压缩: %s · 当前约%s字符[/]" % (
                ("%d处" % n) if n else "无需压缩",
                format(sum(len(str(m.get("content") or "")) for m in messages), ",")))
            continue
        if cmd.startswith("/export"):
            p = export_session(messages, cmd[7:].strip() or None)
            console.print("[cyan]会话已导出: %s[/]" % p)
            continue
        if cmd == "/undo":
            if not any(m.get("role") == "user" for m in messages):
                console.print("[dim]没有可撤销的消息[/]")
                continue
            popped = []
            while messages and messages[-1].get("role") != "user":
                popped.append(messages.pop())
            popped.append(messages.pop())
            undo_stack.append(popped)
            console.print("[yellow]已撤销最近一次提问及其回答(/redo恢复):[/]")
            console.print("[dim]%s[/]" % str(popped[-1].get("content"))[:300])
            continue
        if cmd == "/redo":
            if not undo_stack:
                console.print("[dim]没有可恢复的消息[/]")
                continue
            popped = undo_stack.pop()
            messages.extend(reversed(popped))
            console.print("[cyan]已恢复撤销的消息(%d条)[/]" % len(popped))
            continue
        if cmd.startswith("/btw"):
            question = cmd[4:].strip()
            if not question:
                console.print("[dim]用法: /btw <问题>[/]")
                continue
            with console.status("[dim][btw]思考旁路问题...[/]"):
                try:
                    ans = llm_ask(dict(profiles[active],
                                       **{"model": model_override or profiles[active].get("model")}),
                                 active, list(messages) + [{"role": "user", "content":
                                                             "[旁路提问, 不进入主上下文] " + question}])
                    console.print(Markdown("[btw·旁路回答·未进入主上下文]\n\n" + ans))
                except Exception as e:
                    console.print("[red][btw] 失败: %s[/]" % e)
            continue
        # !shell模式 / @文件附件
        if user.startswith("!") and len(user) > 1:
            cmdline = user[1:].strip()
            console.print("[cyan]$ %s[/]" % cmdline)
            out, err = run_shell(cmdline)
            result = out if out else (err or "(无输出)")
            console.print("[dim]%s[/]" % result[:4000])
            messages.append({"role": "user", "content": "[!shell输出] %s\n%s" % (cmdline, result[:8000])})
            continue
        user = expand_at_files(user)
        slog.log("用户", user)
        messages.append({"role": "user", "content": user})
        eff_profile = dict(profiles[active])
        if model_override:
            eff_profile["model"] = model_override
        try:
            answer, n_tools, n_think, dt = run_agent(
                eff_profile, active, messages, slog, ui,
                tools_list=toolset(mode), allow_exec=(mode == "execute"),
                session_tool_log=session_tool_log)
        except KeyboardInterrupt:
            console.print("\n[yellow]本轮已中断(上下文保留)[/]")
            continue
        except Exception as e:
            console.print("[red]系统错误[/] %s: %s" % (type(e).__name__, e))
            continue
        console.print(Rule("[dim]用时 %.1fs · 工具%d次 · 思考%s字 · 日志 %s[/]" % (
            dt, n_tools, format(n_think, ","), os.path.basename(slog.path)), style="dim"))
        console.print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n退出。")
