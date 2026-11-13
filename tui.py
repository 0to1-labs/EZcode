#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SecOps Agent TUI v0.6 - 全屏OpenCode风格终端
Enter=发送 · Shift/Ctrl/Alt+Enter=换行 · Ctrl+Q=退出
布局: 标题栏 / 会话区 / 实时区(转圈+思考尾+流式正文) / 输入框 / 状态栏
"""
import sys
import os
import json
import time
import threading
import contextlib

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from textual.app import App, ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import (Header, Footer, RichLog, TextArea, Static, Markdown,
                             LoadingIndicator, OptionList, Input)
from textual.widgets.option_list import Option
from textual.binding import Binding
from textual.message import Message
from rich.text import Text as RText
from rich.markdown import Markdown as RMarkdown
from rich.rule import Rule as RRule
from rich.panel import Panel as RPanel

import agent as A
import verify
import plugin_loader
from tools import toolset


class ChatLog(RichLog):
    """会话区RichLog: 富文本可拖选(textual 8.2.8原生get_selection对RichVisual返回None,
    此处从渲染行Strips提取纯文本交给Selection.extract——Pilot端到端实测可提取Markdown渲染内容)。"""

    def get_selection(self, selection):
        try:
            if not self.lines:
                return None
            text = "\n".join((s.text or "").rstrip() for s in self.lines)
            return selection.extract(text), "\n"
        except Exception:
            return None


class ModelPickerScreen(ModalScreen):
    """OpenCode风格 /models 模型选择器: ↑↓选择 · Enter确认 · ESC取消"""
    CSS = """
    ModelPickerScreen { align: center middle; }
    #picker-box { width: 64; height: auto; max-height: 24; border: thick $border;
                  background: $surface; padding: 1; }
    #picker-title { color: $warning; text-style: bold; }
    #model_list { height: auto; max-height: 18; }
    """
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, entries, current_key):
        super().__init__()
        self.entries = entries            # [(key, model, profile)]
        self.current_key = current_key
        self._result = None

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static("选择模型  (↑↓ 选择 · Enter 确认 · ESC 取消)", id="picker-title")
            opts = []
            for i, (key, model, profile) in enumerate(self.entries):
                mark = "▸ " if key == self.current_key else "  "
                label = "%s%s  [dim](%s)[/]" % (mark, model, profile)
                opts.append(Option(label, id=key))
            yield OptionList(*opts, id="model_list")

    def on_mount(self) -> None:
        ol = self.query_one("#model_list", OptionList)
        for i, (key, _, _) in enumerate(self.entries):
            if key == self.current_key:
                ol.highlighted = i
                break

    def on_option_list_option_selected(self, event) -> None:
        self._result = event.option.id
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class LeaderScreen(ModalScreen):
    """Ctrl+X 组合键(OpenCode leader key): 底部提示条, N/L/U/R/M/A触发, ESC取消"""
    CSS = """
    LeaderScreen { align: center bottom; }
    #leader-bar { width: auto; height: 3; border: round $border; background: $surface;
                  padding: 0 2; margin: 0 1 1 0; }
    """
    BINDINGS = [
        Binding("escape", "cancel", "取消"),
        Binding("n", "pick('n')", "新会话", show=False),
        Binding("l", "pick('l')", "会话", show=False),
        Binding("u", "pick('u')", "撤销", show=False),
        Binding("r", "pick('r')", "恢复", show=False),
        Binding("m", "pick('m')", "模型", show=False),
        Binding("a", "pick('a')", "模式", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="leader-bar"):
            yield Static("Ctrl+X:  [N]新会话  [L]会话列表  [U]撤销  [R]恢复  [M]模型  [A]模式  [ESC]取消")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_pick(self, key: str) -> None:
        self.dismiss(key)


class PaletteInput(Input):
    """Ctrl+P面板的过滤输入框: ↑↓导航列表, Enter选中, ESC取消"""
    BINDINGS = [
        Binding("up", "nav_up", show=False),
        Binding("down", "nav_down", show=False),
        Binding("enter", "pick", show=False),
    ]

    def action_nav_up(self) -> None:
        self.screen.query_one("#p-list", OptionList).action_cursor_up()

    def action_nav_down(self) -> None:
        self.screen.query_one("#p-list", OptionList).action_cursor_down()

    def action_pick(self) -> None:
        ol = self.screen.query_one("#p-list", OptionList)
        try:
            name = str(ol.get_option_at_index(ol.highlighted).id)
        except Exception:
            return
        self.screen.dismiss(name)


class PaletteScreen(ModalScreen):
    """Ctrl+P 命令面板(OpenCode V2: every action available)"""
    CSS = """
    PaletteScreen { align: center middle; }
    #palette-box { width: 70; height: auto; max-height: 26; border: thick $border;
                   background: $surface; padding: 1; }
    #p-filter { border: round $border; margin-bottom: 1; }
    #p-list { height: auto; max-height: 18; }
    """
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, commands):
        super().__init__()
        self.commands = commands   # [(name, desc, has_args)]

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-box"):
            yield PaletteInput(id="p-filter", placeholder="输入过滤命令...")
            yield OptionList(id="p-list")

    def on_mount(self) -> None:
        self._populate("")
        self.query_one("#p-filter", PaletteInput).focus()

    def _populate(self, q):
        ol = self.query_one("#p-list", OptionList)
        ol.clear_options()
        q = q.strip().lower().lstrip("/")
        matched = []
        for n, d, a in self.commands:
            if q and q not in n.lower() and q not in d.lower():
                continue
            matched.append((n, d, a))
        # 排序: 精确名匹配优先, 短名优先(输入model时/model排在/models前)
        matched.sort(key=lambda x: (0 if x[0].lstrip("/") == q else 1, len(x[0])))
        for n, d, a in matched:
            ol.add_option(Option("%s [dim]%s%s[/]" % (n, d, " <参数>" if a else ""), id=n))
        ol.highlighted = 0

    def on_input_changed(self, event) -> None:
        self._populate(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class GenericPickerScreen(ModalScreen):
    """通用选择器(会话/agent等): ↑↓选择 · Enter确认 · ESC取消"""
    CSS = """
    GenericPickerScreen { align: center middle; }
    #gpicker-box { width: 64; height: auto; max-height: 24; border: thick $border;
                   background: $surface; padding: 1; }
    #gpicker-title { color: $warning; text-style: bold; }
    #gpicker-list { height: auto; max-height: 18; }
    """
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, title, entries, current_id=None):
        super().__init__()
        self.title_text = title
        self.entries = entries            # [(id, label)]
        self.current_id = current_id

    def compose(self) -> ComposeResult:
        with Vertical(id="gpicker-box"):
            yield Static(self.title_text + "  (↑↓ 选择 · Enter 确认 · ESC 取消)", id="gpicker-title")
            opts = [Option(label, id=str(id_)) for id_, label in self.entries]
            yield OptionList(*opts, id="gpicker-list")

    def on_mount(self) -> None:
        ol = self.query_one("#gpicker-list", OptionList)
        for i, (id_, _) in enumerate(self.entries):
            if str(id_) == str(self.current_id):
                ol.highlighted = i
                break

    def on_option_list_option_selected(self, event) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen):
    """权限ask确认框: 允许这一次 / 本会话总是允许 / 拒绝"""
    CSS = """
    ConfirmScreen { align: center middle; }
    #confirm-box { width: 64; height: auto; max-height: 16; border: thick $warning;
                   background: $surface; padding: 1; }
    #confirm-detail { height: auto; max-height: 8; color: $text; }
    #confirm-list { height: auto; max-height: 6; }
    """
    BINDINGS = [("escape", "no", "拒绝")]

    def __init__(self, action, resource, detail):
        super().__init__()
        self.action = action
        self.resource = resource
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static("⚠ 权限确认  [dim](↑↓ 选择 · Enter 确认 · ESC 拒绝)[/]",
                         id="gpicker-title")
            yield Static(self.detail[:500], id="confirm-detail")
            yield OptionList(
                Option("✓ 允许这一次", id="once"),
                Option("✓ 本会话总是允许该类操作", id="always"),
                Option("✗ 拒绝", id="no"),
                id="confirm-list")

    def on_option_list_option_selected(self, event) -> None:
        self.dismiss(event.option.id)

    def action_no(self) -> None:
        self.dismiss("no")


class InputArea(TextArea):
    """输入框(OpenCode V2键位): Enter=发送; Shift/Ctrl/Alt+Enter或Ctrl+J=换行"""
    BINDINGS = [
        Binding("enter", "enter_key", "发送", priority=True),
        Binding("ctrl+o", "open_answer", "打开最近回答", priority=True),
        Binding("shift+enter", "newline", "换行", show=False),
        Binding("ctrl+enter", "newline", "换行", show=False),
        Binding("alt+enter", "newline", "换行", show=False),
        Binding("ctrl+j", "newline", "换行", show=False),
    ]

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class CmdNav(Message):
        """命令框内导航(↑↓)"""

        def __init__(self, key: str) -> None:
            super().__init__()
            self.key = key

    class CmdPicked(Message):
        """命令框内回车选中"""

        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_enter = 0.0

    def _cmd_ctx(self):
        try:
            app = self.screen.app
            if getattr(app, "cmd_box_open", False):
                return app
        except Exception:
            pass
        return None

    def on_key(self, event) -> None:
        """命令下拉框打开时拦截 ↑↓(Enter由优先绑定处理, ESC由App层分发)"""
        app = self._cmd_ctx()
        if app is not None:
            if event.key in ("up", "down"):
                app.post_message(self.CmdNav(event.key))
                event.stop()
                event.prevent_default()
                return
        # 其余键不拦截, 交回TextArea默认行为(勿调super——Widget无on_key定义)

    def action_enter_key(self) -> None:
        # 命令框打开: Enter = 选中高亮命令执行(OpenCode命令面板语义)
        app = self._cmd_ctx()
        if app is not None:
            app.post_message(self.CmdPicked(getattr(app, "cmd_highlighted_name", "")))
            return
        # OpenCode V2: Enter = 直接发送(单行/多行一致)
        self.action_submit()

    def action_submit(self) -> None:
        text = self.text
        if text.strip():
            self.post_message(self.Submitted(text))
        try:
            self.clear()
        except Exception:
            self.text = ""

    def action_newline(self) -> None:
        try:
            self.insert("\n")
        except Exception:
            self.text = self.text + "\n"

    def action_open_answer(self) -> None:
        """Ctrl+O: 最近回答写入文件并用系统默认编辑器打开(终端无关的可选中复制方案)"""
        app = self.screen.app
        app._open_answer_in_editor()


class TuiUI:
    """引擎事件 -> TUI线程安全的UI刷新(经call_from_thread)"""

    def __init__(self, app: "SecOpsApp"):
        self.app = app
        self._t0 = time.time()
        self._last_md = 0.0

    def _post(self, fn, *a):
        try:
            self.app.call_from_thread(fn, *a)
        except Exception:
            pass

    # --- 引擎事件 ---
    def stream_start(self):
        self._t0 = time.time()
        self._post(self.app._live_reset)

    def reason_delta(self, buf):
        self._post(self.app._reason_update, len(buf), buf[-400:])

    def reason_done(self, total, elapsed):
        self._post(self.app._reason_done, total, elapsed)

    def content_start(self):
        self._post(self.app._content_start)

    def content_delta(self, buf):
        now = time.time()
        if now - self._last_md < 0.25:   # 节流, 避免高频重渲染
            return
        self._last_md = now
        self._post(self.app._content_update, buf)

    def content_end(self, buf):
        self._post(self.app._content_end, buf)

    def tool_start(self, fname, args_str):
        self._post(self.app._tool_line,
                   RText("⚙ %s(%s)" % (fname, args_str[:100]), style="cyan"))
        self._post(self.app._status_update, "⚙ %s 执行中..." % fname, "cyan")

    @contextlib.contextmanager
    def tool_running(self, fname):
        yield

    def sub_progress(self, text):
        """子代理进度心跳: 更新状态条(经call_from_thread, 线程安全)"""
        self._post(self.app._status_update, text, "cyan")

    def tool_done(self, fname, ok, dt, size, err=None):
        if ok:
            line = "  └ ✓ %s · %.1fs · 结果%s字符" % (fname, dt, format(size, ","))
            style = "green"
        else:
            line = "  └ ✗ %s · %.1fs · %s" % (fname, dt, str(err)[:120])
            style = "red"
        self._post(self.app._tool_line, RText(line, style=style))

    def round_done(self, answer, dt, n_tools, n_think, slog):
        pass  # 由app._round_done收尾

    def gate_report(self, gate):
        self._post(self.app._gate_report, gate)


class SecOpsApp(App):
    TITLE = "EZcode"
    CSS = """
    #chat { height: 1fr; border: round $border; padding: 0 1; }
    #livebox { height: auto; padding: 0 1; }
    #spinner { height: 1; width: auto; color: $success; }
    #thinkrow { height: auto; }
    #thinkline { height: 1; color: $warning; }
    #thinktail { height: auto; max-height: 6; color: $text-muted; }
    #livemd { height: auto; max-height: 40; padding: 0 1; border-top: solid $border; }
    #cmdbox { height: auto; max-height: 12; border: round $border; background: $surface; padding: 0; display: none; }
    #input { height: 6; border: tall $border; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("ctrl+c", "copy_smart", "复制", priority=True),   # App级: 焦点无论在哪都响应(盖住Screen的ctrl+c)
        Binding("tab", "toggle_mode", "切模式", priority=True),
        Binding("shift+tab", "toggle_mode", "切模式", priority=True, show=False),
        Binding("ctrl+x", "leader", "组合键", priority=True),
        Binding("ctrl+p", "palette", "命令面板", priority=True),
        Binding("f2", "cycle_model", "循环模型", priority=True),
        Binding("escape", "esc_abort", "ESC"),
    ]

    def __init__(self, cfg, profiles, active):
        super().__init__()
        self.cfg = cfg
        self.profiles = profiles
        self.active = active
        self.messages = [{"role": "system", "content": A.build_system_prompt(cfg)}]
        self.slog = A.SessionLog()
        self.busy = False
        self.mode = "query"  # query=只读查询(默认) / execute=可执行处置回写
        self.sessions = []          # 已归档会话(/sessions 可切回)
        self._session_tool_log = []   # 会话级工具台账(验证门跨轮重算)
        self._undo_stack = []        # /undo 撤回的消息(/redo 恢复)
        self.cmd_box_open = False    # 斜杠命令下拉框状态
        self.cmd_highlighted_name = ""
        self._t0 = time.time()
        self._last_esc = 0.0
        self._cancel_event = threading.Event()

    # ---------- 会话持久化(logs/sessions/, 跨启动可恢复) ----------
    SESSIONS_DIR = os.path.join(BASE_DIR, "logs", "sessions")

    def _session_file(self, slog_path):
        return os.path.join(self.SESSIONS_DIR,
                            os.path.basename(slog_path).rsplit(".", 1)[0] + ".session.json")

    def _save_current_session(self):
        """当前会话落盘(messages快照)。有user消息才有保存价值。"""
        if len([m for m in self.messages if m.get("role") == "user"]) == 0:
            return
        try:
            os.makedirs(self.SESSIONS_DIR, exist_ok=True)
            data = {"title": getattr(self, "session_title", "") or "未命名会话",
                    "messages": self.messages,
                    "slog_path": self.slog.path,
                    "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            with open(self._session_file(self.slog.path), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=str)
        except Exception:
            pass   # 保存失败不阻断对话

    def _disk_sessions(self):
        """磁盘上的历史会话(排除已在内存归档里的, 按保存时间倒序)"""
        out = []
        try:
            known = {os.path.normcase(s["slog_path"]) for s in self.sessions}
            files = sorted(os.listdir(self.SESSIONS_DIR), reverse=True)
        except Exception:
            return out
        for fn in files:
            if not fn.endswith(".session.json"):
                continue
            p = os.path.join(self.SESSIONS_DIR, fn)
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                if os.path.normcase(d.get("slog_path") or "") in known:
                    continue
                n = len([m for m in (d.get("messages") or [])
                         if m.get("role") in ("user", "assistant")])
                if n == 0:
                    continue
                out.append({"disk": p, "file": fn,
                            "title": d.get("title") or "未命名会话",
                            "messages": d["messages"],
                            "slog_path": d["slog_path"],
                            "saved_at": d.get("saved_at", ""),
                            "n_msgs": n})
            except Exception:
                continue
        return out

    # 斜杠命令注册表(OpenCode /命令面板数据源): (名称, 说明, 是否带参数)
    COMMANDS = [
        ("/new", "新会话(旧会话自动归档)", False),
        ("/sessions", "切回历史会话", False),
        ("/undo", "撤销最近一次提问+回答", False),
        ("/redo", "恢复撤销的消息", False),
        ("/btw", "旁路提问, 回答不进主上下文", True),
        ("/models", "模型选择器(实时发现可用模型)", False),
        ("/model", "文字式查看/切换模型", True),
        ("/agents", "主agent模式选择器", False),
        ("/summary", "生成会话摘要", False),
        ("/compact", "手动压缩上下文", False),
        ("/export", "导出会话为markdown", True),
        ("/status", "运行状态总览", False),
        ("/copy", "复制最近回答到剪贴板", False),
        ("/open", "在系统编辑器中打开最近回答(可选中复制)", False),
        ("/help", "帮助", False),
        ("/exit", "退出", False),
    ]

    # ---------- 斜杠命令下拉框(OpenCode语义: 输入/即弹, 边打边过滤, Enter执行) ----------
    def cmd_close(self):
        self.cmd_box_open = False
        try:
            self.query_one("#cmdbox", OptionList).display = False
        except Exception:
            pass

    def _update_cmd_box(self, text):
        try:
            ol = self.query_one("#cmdbox", OptionList)
        except Exception:
            return
        t = text.strip()
        if not t.startswith("/") or "\n" in text or " " in text:
            self.cmd_close()
            return
        matches = [(n, d, a) for (n, d, a) in self.COMMANDS if n.startswith(t)]
        if not matches:
            self.cmd_close()
            return
        matches.sort(key=lambda x: (0 if x[0] == t else 1, x[0]))
        ol.clear_options()
        for n, d, a in matches:
            ol.add_option(Option("%s [dim]%s%s[/]" % (n, d, " <参数>" if a else ""), id=n))
        ol.highlighted = 0
        ol.display = True
        self.cmd_box_open = True
        self.cmd_highlighted_name = matches[0][0]

    def on_text_area_changed(self, event):
        """输入变化 -> 更新命令下拉框过滤(Changed消息namespace属TextArea, 处理器名按定义类)"""
        text = None
        for attr in ("text", "text_area"):
            try:
                v = getattr(event, attr)
                if attr == "text_area":
                    v = v.text
                text = v
                break
            except Exception:
                continue
        if text is None:
            try:
                text = self.query_one("#input", InputArea).text
            except Exception:
                return
        self._update_cmd_box(text)

    def on_input_area_cmd_nav(self, event):
        ol = self.query_one("#cmdbox", OptionList)
        if event.key == "down":
            ol.action_cursor_down()
        else:
            ol.action_cursor_up()
        try:
            self.cmd_highlighted_name = str(ol.get_option_at_index(ol.highlighted).id)
        except Exception:
            pass

    def on_input_area_cmd_picked(self, event):
        name = event.name
        ia = self.query_one("#input", InputArea)
        spec = next((s for s in self.COMMANDS if s[0] == name), None)
        self.cmd_close()
        try:
            ia.clear()
        except Exception:
            ia.text = ""
        if not name:
            return
        if spec and spec[2]:
            # 带参数命令: 填入前缀继续输入
            ia.text = name + " "
            ia.focus()
            return
        if not self._handle_command(name):
            self._chat_write(RText("未知命令: %s" % name, style="red"))

    def _set_mode(self, mode):
        self.mode = mode
        if mode == "execute":
            self.messages.append({"role": "system", "content": (
                "[模式切换] 可执行模式: 解锁ngsoc_dispose_alarms(告警平台告警处置回写)与kb_add_entry(知识沉淀)。"
                "执行前必须复述内容并获用户明确同意后带confirmed=true调用; "
                "误报定性需报文级证据(判据R-001)")})
            self._chat_write(RText("⇄ 已切换到【可执行模式】: 可执行告警平台告警处置回写(逐次需确认)", style="bold orange3"))
        else:
            self.messages.append({"role": "system", "content": (
                "[模式切换] 只读查询模式: 不执行任何处置回写, 只做查询研判")})
            self._chat_write(RText("⇄ 已切换到【只读查询模式】: 仅查询研判, 不执行处置", style="cyan"))
        self._refresh_mode_label()

    def _on_agent_picked(self, mode):
        """主agent模式选择器回填(/agents)"""
        if not mode or mode == self.mode:
            return
        self._set_mode(mode)

    def _open_agents(self):
        entries = [("query", "只读查询模式  [dim]仅查询研判, 处置类工具禁用[/]"),
                   ("execute", "可执行模式  [dim]解锁处置回写+知识沉淀+shell(逐次需确认)[/]")]
        self.push_screen(GenericPickerScreen("选择主agent模式", entries, self.mode),
                         self._on_agent_picked)

    # ---------- Ctrl+X组合键(OpenCode leader key)与F2模型循环 ----------
    # ---------- Ctrl+P命令面板(OpenCode V2)与上下文占用 ----------
    def action_palette(self) -> None:
        if self.busy:
            self._chat_write(RText("[处理中, 请稍候]", style="yellow"))
            return
        self.push_screen(PaletteScreen(self.COMMANDS), self._on_palette)

    def _on_palette(self, name):
        if not name:
            return
        self._run_command_by_name(name)

    def _run_command_by_name(self, name):
        """按命令名执行(零参直接跑, 带参填前缀)——命令面板/CmdPicked共用"""
        spec = next((s for s in self.COMMANDS if s[0] == name), None)
        if spec and spec[2]:
            ia = self.query_one("#input", InputArea)
            ia.text = name + " "
            ia.focus()
            return
        if not self._handle_command(name):
            self._chat_write(RText("未知命令: %s" % name, style="red"))

    def _context_usage(self):
        """上下文占用估算: 字符×0.55≈token(中英混合), 与profile.context限制对比"""
        eff = self._effective_profile()
        try:
            limit = int(eff.get("context") or 128000)
        except Exception:
            limit = 128000
        total_chars = sum(len(str(m.get("content") or "")) for m in self.messages)
        est_tokens = int(total_chars * 0.55)
        pct = min(100, est_tokens * 100 // max(limit, 1))
        return est_tokens, limit, pct

    # ---------- 可靠复制(Win32剪贴板API优先, OSC52降级, 文件兜底) ----------
    def action_copy_smart(self) -> None:
        """App级Ctrl+C智能复制(三级): ①会话区拖选→复制选区 ②输入框选中→复制 ③都无→弹复制选择器"""
        if len(self.screen_stack) > 1:
            return   # 弹窗(选择器/确认窗)打开时不抢Ctrl+C
        try:
            sel = self.screen.get_selected_text()
        except Exception:
            sel = None
        if sel:
            self._copy_text(sel, "选中文本")
            try:
                self.screen.clear_selection()
            except Exception:
                pass
            return
        try:
            st = self.query_one("#input", InputArea).selected_text
        except Exception:
            st = ""
        if st:
            self._copy_text(st, "输入框选中")
            return
        self._open_copy_picker()   # 不依赖拖选: 菜单选要复制什么
    @staticmethod
    def _win32_copy(text):
        """Win32剪贴板API(ctypes直调, Unicode保真, ~1ms, 无子进程无版本兼容问题)。仅Windows。
        注意: 64位进程必须显式声明restype, 否则指针被截断成int导致句柄错误。"""
        try:
            import ctypes
            c_void_p, c_size_t, c_uint = ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            kernel32.GlobalAlloc.restype = c_void_p
            kernel32.GlobalAlloc.argtypes = [c_uint, c_size_t]
            kernel32.GlobalLock.restype = c_void_p
            kernel32.GlobalLock.argtypes = [c_void_p]
            kernel32.GlobalUnlock.argtypes = [c_void_p]
            kernel32.GlobalFree.restype = c_void_p
            kernel32.GlobalFree.argtypes = [c_void_p]
            user32.OpenClipboard.argtypes = [c_void_p]
            user32.SetClipboardData.restype = c_void_p
            user32.SetClipboardData.argtypes = [c_uint, c_void_p]
            CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
            data = (str(text) + "\0").encode("utf-16-le")
            for _ in range(4):    # 剪贴板可能被其他进程短暂占用, 小重试
                if not user32.OpenClipboard(None):
                    time.sleep(0.05)
                    continue
                try:
                    user32.EmptyClipboard()
                    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                    if not h:
                        return False
                    p = kernel32.GlobalLock(h)
                    if not p:
                        kernel32.GlobalFree(h)
                        return False
                    ctypes.memmove(p, data, len(data))
                    kernel32.GlobalUnlock(h)
                    ok = bool(user32.SetClipboardData(CF_UNICODETEXT, h))
                    if not ok:
                        kernel32.GlobalFree(h)   # 失败时系统未接管, 自行释放
                    return ok
                finally:
                    user32.CloseClipboard()
            return False
        except Exception:
            return False

    def _copy_text(self, text, label="文本"):
        """复制text到系统剪贴板。通道: ①Win32 API(Windows, 最可靠)
        ②OSC52(textual内置, 依赖终端实现) ③兜底文件logs/last_answer.txt(永远写)。"""
        text = str(text or "")
        if not text:
            self.notify("没有可复制的%s" % label, severity="warning")
            return
        p = os.path.join(A.BASE_DIR, "logs", "last_answer.txt")
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass
        how = None
        if os.name == "nt" and self._win32_copy(text):
            how = "系统剪贴板"
        if how is None:
            try:
                self.copy_to_clipboard(text)     # OSC52
                how = "OSC52剪贴板"
            except Exception:
                how = None
        if how is None:
            self.notify("复制失败, 内容已在 logs/last_answer.txt", severity="warning")
        else:
            self.notify("已复制%s(%d字符) → %s · 兜底: logs/last_answer.txt"
                        % (label, len(text), how), timeout=6)

    def _last_rounds_text(self, n):
        """最近n轮对话纯文本(user/assistant/工具结果; 不含system与模式注入)"""
        out, count = [], 0
        for m in reversed(self.messages):
            r = m.get("role")
            if r == "assistant" and m.get("content"):
                count += 1
                if count > n:
                    break
            if r == "user":
                out.append("【你】\n%s" % (m.get("content") or ""))
            elif r == "assistant" and m.get("content"):
                out.append("【EZcode】\n%s" % m.get("content"))
            elif r == "tool":
                out.append("【工具结果】\n%s" % (m.get("content") or ""))
        return "\n\n".join(reversed(out))

    def _open_copy_picker(self):
        """Ctrl+C无选区时: 弹复制选择器(完全不依赖终端拖选的可靠复制路径)"""
        entries = [("last", "复制最近回答"),
                   ("r3", "复制最近 3 轮对话(含工具结果)"),
                   ("r10", "复制最近 10 轮对话(含工具结果)"),
                   ("all", "复制全部会话内容"),
                   ("tool", "复制最近工具结果(证据)")]
        self.push_screen(GenericPickerScreen("复制内容(↑↓选择 · Enter确认)", entries, "last"),
                         self._copy_picked)

    def _copy_picked(self, which):
        if not which:
            return
        if which == "last":
            last = next((m.get("content") for m in reversed(self.messages)
                         if m.get("role") == "assistant" and m.get("content")), "")
            self._copy_text(last, "最近回答")
        elif which == "r3":
            self._copy_text(self._last_rounds_text(3), "最近3轮")
        elif which == "r10":
            self._copy_text(self._last_rounds_text(10), "最近10轮")
        elif which == "all":
            self._copy_text(self._last_rounds_text(10 ** 6), "全部会话")
        elif which == "tool":
            last = next((m.get("content") for m in reversed(self.messages)
                         if m.get("role") == "tool" and m.get("content")), "")
            self._copy_text(last, "最近工具结果")

    def _cmd_copy(self):
        """/copy: 复制最近回答(可靠剪贴板通道)"""
        last = next((m.get("content") for m in reversed(self.messages)
                     if m.get("role") == "assistant" and m.get("content")), "")
        if not last:
            self._chat_write(RText("没有可复制的回答", style="dim"))
            return
        self._copy_text(last, "最近回答")

    def _open_answer_in_editor(self):
        """Ctrl+O: 最近回答写入文件并用系统默认编辑器打开(记事本里可正常选中复制)"""
        last = next((m.get("content") for m in reversed(self.messages)
                     if m.get("role") == "assistant" and m.get("content")), "")
        if not last:
            self._chat_write(RText("没有可打开的回答", style="dim"))
            return
        p = os.path.join(A.BASE_DIR, "logs", "last_answer.txt")
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(last)
        except Exception as e:
            self._chat_write(RText("写入失败: %s" % e, style="red"))
            return
        try:
            os.startfile(p)   # Windows默认.txt编辑器(通常是记事本)
            self._chat_write(RText("已在系统编辑器打开最近回答(logs/last_answer.txt), 可直接选中复制",
                                   style="cyan"))
        except Exception as e:
            self._chat_write(RText("打开编辑器失败(%s), 文件在: %s" % (e, p), style="yellow"))

    def action_leader(self) -> None:
        if self.busy:
            self._chat_write(RText("[处理中, 请稍候]", style="yellow"))
            return
        self.push_screen(LeaderScreen(), self._on_leader)

    def _on_leader(self, key):
        if not key:
            return
        mapping = {"n": self._new_session, "l": self._open_sessions,
                   "u": self._cmd_undo, "r": self._cmd_redo,
                   "m": self.action_open_models, "a": self._open_agents}
        fn = mapping.get(key)
        if fn:
            fn()

    def action_cycle_model(self) -> None:
        """F2循环最近模型(OpenCode V2语义)"""
        cur_key = "%s::%s" % (self.active,
                              getattr(self, "model_override", None)
                              or self.profiles[self.active].get("model"))
        rec = getattr(self, "recent_models", None)
        if not rec:
            rec = ["%s::%s" % (k, v.get("model")) for k, v in self.profiles.items()]
            if cur_key not in rec:
                rec.insert(0, cur_key)
            self.recent_models = rec
        i = rec.index(cur_key) if cur_key in rec else 0
        nxt = rec[(i + 1) % len(rec)]
        name, model = nxt.split("::", 1)
        self.active = name
        self.model_override = model if model != self.profiles[name].get("model") else None
        self._chat_write(RText("模型切换: [bold]%s[/] (%s)" % (model, name), style="cyan"))

    # ---------- 权限确认桥(引擎线程 -> TUI弹窗) ----------
    def _confirm_bridge(self, action, resource, detail):
        """供权限引擎调用的确认回调(在引擎线程中执行): 弹窗等待用户点选"""
        ev = threading.Event()
        box = {"ok": False, "always": False}

        def on_result(choice):
            box["always"] = (choice == "always")
            box["ok"] = (choice in ("once", "always"))
            ev.set()

        self.call_from_thread(self._push_confirm, action, resource, detail, on_result)
        ev.wait(timeout=600)
        if box["ok"] and box["always"]:
            from tools import permissions as perm
            perm.grant_session(action, resource)
        return box["ok"]

    def _push_confirm(self, action, resource, detail, on_result):
        self.push_screen(ConfirmScreen(action, resource, detail), on_result)

    def action_toggle_mode(self) -> None:
        self._set_mode("execute" if self.mode == "query" else "query")

    async def action_esc_abort(self) -> None:
        """ESC兜底(模态屏自身的ESC绑定优先; 剩命令框与双击中止)"""
        if len(self.screen_stack) > 1:      # 有模态弹窗: 关闭最上层
            try:
                await self.screen.dismiss(None)
            except Exception:
                pass
            return
        if getattr(self, "cmd_box_open", False):
            self.cmd_close()
            return
        now = time.monotonic()
        if now - self._last_esc <= 1.0:
            self._last_esc = 0.0
            if self.busy:
                self._cancel_event.set()
                self._chat_write(RText("⏹ 已请求中止本轮(等待当前步骤结束, 长工具如evidence限频需等它跑完)...",
                                       style="yellow"))
            else:
                self._chat_write(RText("(当前无进行中的回答)", style="dim"))
        else:
            self._last_esc = now
            if self.busy:
                self._chat_write(RText("…再按一次ESC中止本轮", style="dim"))

    # ---------- 布局 ----------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        # ChatLog: 可拖选的富文本会话区; auto_scroll关掉改为智能跟随(翻历史不被拽走)
        yield ChatLog(id="chat", wrap=True, auto_scroll=False)
        with Vertical(id="livebox"):
            with Horizontal(id="thinkrow"):
                yield LoadingIndicator(id="spinner")
                yield Static("", id="thinkline")
            yield Static("", id="thinktail")
            yield Markdown("", id="livemd")
        yield OptionList(id="cmdbox")
        yield InputArea(id="input")
        yield Footer()

    def _mode_label(self):
        if self.mode == "execute":
            return RText("可执行模式", style="orange3")
        return RText("只读查询模式", style="cyan")

    def _refresh_mode_label(self):
        title = getattr(self, "session_title", "")
        _, _, pct = self._context_usage()
        busy = getattr(self, "busy", False)
        parts = []
        if busy:
            parts.append(RText("⏳ 运行中 · ", style="orange3"))
        parts.append(RText(" · ~%d%%上下文" % pct, style="dim"))
        if title:
            parts.append(RText(" · " + title, style="dim"))
        self.query_one("#input", InputArea).border_title = RText.assemble(
            self._mode_label(), *parts)
        self.sub_title = ""   # 标题栏只留 EZcode

    def on_mount(self) -> None:
        p = self.profiles[self.active]
        chat = self.query_one("#chat", RichLog)
        chat.write(RText("EZcode v1.2 · 安全运营研判工作台", style="bold cyan"))
        st = plugin_loader.status()
        mcp_part = ("MCP%s工具" % st["mcp_tools"]) if plugin_loader.is_mcp_done() else "MCP后台连接中"
        from tools import ngsoc as _ngsoc
        n_sites = len(_ngsoc._sites)
        n_edr = len((A.RUNTIME.get("cfg") or {}).get("edr_projects") or {})
        chat.write(RText("模型: %s (%s) · %s · %s · 扩展: 插件%s工具/%s · 日志: %s"
                         % (p.get("model"), self.active,
                            ("%d个告警平台站点" % n_sites) if n_sites else "告警平台未配置",
                            ("%d个主机侧项目" % n_edr) if n_edr else "主机侧未配置",
                            st["plugin_tools"], mcp_part,
                            os.path.basename(self.slog.path)),
                         style="dim"))
        for w in plugin_loader.warnings():
            chat.write(RText("[扩展加载] " + w, style="yellow"))
        if not plugin_loader.is_mcp_done():
            self._mcp_timer = self.set_interval(0.5, self._check_mcp_ready)
        chat.write(RText("Enter=发送 · Shift/Ctrl/Alt+Enter或Ctrl+J=换行 · ESC关弹窗(运行中双击中止) · Tab/Shift+Tab切模式 · Ctrl+X组合键 · F2换模型 · Ctrl+Q 退出",
                         style="dim"))
        chat.write(RText("复制: 会话区鼠标拖选后Ctrl+C · 或直接按Ctrl+C弹复制菜单(最近回答/最近N轮/全部/工具证据) · Ctrl+O在编辑器打开最近回答",
                         style="dim"))
        if not os.environ.get("WT_SESSION") and os.name == "nt" and "TERM" not in os.environ:
            chat.write(RText("⚠ 未检测到 Windows Terminal: 传统控制台对鼠标拖选支持有限——"
                             "若拖选无高亮, 复制请直接按Ctrl+C走复制菜单; 建议在 Windows Terminal 中运行 start.bat",
                             style="yellow"))
        chat.write(RText(""))
        for wid in ("#spinner", "#livemd"):
            self.query_one(wid).display = False
        self.query_one("#cmdbox", OptionList).display = False
        self.cmd_box_open = False
        self.query_one("#thinktail", Static).display = False
        self._refresh_mode_label()
        self.query_one("#input", InputArea).focus()

    def _check_mcp_ready(self) -> None:
        """MCP后台连接完成提示(工具下一轮生效); 同步刷新主会话system prompt让模型知晓扩展工具"""
        if not plugin_loader.is_mcp_done():
            return
        st = plugin_loader.status()
        if st["mcp_servers"]:
            self._chat_write(RText("MCP就绪: %s (注册%s个工具, 下一轮对话可用)"
                                   % (", ".join(st["mcp_servers"]), st["mcp_tools"]),
                                   style="dim cyan"))
        for w in plugin_loader.warnings():
            if "连接失败" in w or "配置项异常" in w:
                self._chat_write(RText("[MCP] " + w, style="yellow"))
        self._chat_write(RText(""))
        # 刷新主会话system prompt(启动时构建的版本不含MCP工具说明)
        try:
            if self.messages and self.messages[0].get("role") == "system":
                self.messages[0]["content"] = A.build_system_prompt(self.cfg)
        except Exception:
            pass
        if getattr(self, "_mcp_timer", None):
            self._mcp_timer.stop()
            self._mcp_timer = None

    # ---------- UI线程方法(由call_from_thread调) ----------
    def _chat_write(self, renderable):
        chat = self.query_one("#chat", RichLog)
        # 智能跟随: 用户在底部→新内容自动跟随; 在翻历史→不打扰(流式期间不再把视图拽到底)
        try:
            at_bottom = chat.scroll_y >= chat.max_scroll_y - 2
        except Exception:
            at_bottom = True
        chat.write(renderable)
        if at_bottom:
            chat.scroll_end(animate=False)

    def _live_reset(self):
        self._t0 = time.time()
        self.query_one("#spinner", LoadingIndicator).display = True
        self.query_one("#thinkline", Static).update(RText("连接模型响应中...", style="yellow"))
        self.query_one("#thinktail", Static).display = False
        self.query_one("#livemd").display = False
        self.query_one("#livemd", Markdown).update("")

    def _reason_update(self, n, tail):
        el = time.time() - self._t0
        if n > 8000:
            line = RText("思考中 · %d字 · %.0fs · 过长, 双击ESC可中止" % (n, el), style="orange3")
        else:
            line = RText("思考中 · %d字 · %.0fs" % (n, el), style="cyan")
        self.query_one("#thinkline", Static).update(line)
        t = self.query_one("#thinktail", Static)
        t.update(RText("  " + tail.replace("\n", " "), style="dim italic"))
        t.display = True

    def _reason_done(self, total, elapsed):
        # busy期间常驻状态: 思考完成后保留摘要行(正文/工具阶段仍有指示)
        line = RText("思考完成 · %d字 · %.1fs" % (total, elapsed), style="dim") if total \
            else RText("等待响应...", style="dim")
        self.query_one("#thinkline", Static).update(line)
        if total:
            self._chat_write(RText("◦ 思考完成 · %d字 · %.1fs" % (total, elapsed), style="dim"))
        self.query_one("#thinktail", Static).display = False

    def _content_start(self):
        self.query_one("#spinner", LoadingIndicator).display = False
        self.query_one("#thinkline", Static).update(RText("生成回答中...", style="cyan"))
        self.query_one("#livemd").display = True

    def _status_update(self, text, style="dim"):
        """busy常驻状态条(thinkline复用)"""
        try:
            self.query_one("#thinkline", Static).update(RText(text, style=style))
        except Exception:
            pass

    def _content_update(self, buf):
        self.query_one("#livemd", Markdown).update(buf)

    def _content_end(self, buf):
        self.query_one("#livemd").display = False
        self._chat_write(RMarkdown(buf))
        self._chat_write("")

    def _tool_line(self, rtext):
        self._chat_write(rtext)

    def _gate_report(self, gate):
        """确定性校验门报告写入会话区(P14.38: agent不能给自己打分)"""
        for f in gate["findings"]:
            style = "red" if f["severity"] == "block" else "yellow"
            mark = "✗ BLOCK" if f["severity"] == "block" else "⚠ WARN"
            self._chat_write(RText("%s %s: %s" % (mark, f["code"], f["text"]), style=style))
        if gate["passed"]:
            if gate["findings"]:
                self._chat_write(RText("✓ 流程校验通过(含%d条提醒)" % len(gate["findings"]), style="green"))
        else:
            self._chat_write(RText("✗ 流程校验未通过 —— 定性应标注'待确认'或补证据后重判", style="red bold"))

    def _effective_profile(self):
        """当前生效profile(含模型覆盖, /models选择器用)"""
        p = dict(self.profiles[self.active])
        ov = getattr(self, "model_override", None)
        if ov:
            p["model"] = ov
        return p

    def action_open_models(self) -> None:
        """OpenCode风格 /models: 后台实时发现各provider可用模型 → 弹出选择器"""
        if self.busy:
            self._chat_write(RText("[处理中, 请稍候]", style="yellow"))
            return

        def discover():
            import concurrent.futures

            def _one(item):
                name, prof = item
                try:
                    return name, A.llm_list_models(prof, name)
                except Exception:
                    return name, []

            entries = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(self.profiles))) as ex:
                for name, models in ex.map(_one, self.profiles.items()):
                    if not models:
                        models = [self.profiles[name].get("model", "?")]
                    for mid in models:
                        entries.append(("%s::%s" % (name, mid), mid, name))
            self.call_from_thread(self._push_picker, entries)

        self._chat_write(RText("发现可用模型中...", style="dim"))
        threading.Thread(target=discover, daemon=True).start()

    def _push_picker(self, entries):
        if not entries:
            self._chat_write(RText("未发现任何模型(检查devices.json llm.profiles)", style="red"))
            return
        ov = getattr(self, "model_override", None)
        current = "%s::%s" % (self.active, ov or self.profiles[self.active].get("model"))
        self.push_screen(ModelPickerScreen(entries, current), self._on_model_picked)

    def _on_model_picked(self, key):
        if not key:
            self._chat_write(RText("(已取消)", style="dim"))
            return
        name, model = key.split("::", 1)
        self.active = name
        self.model_override = model if model != self.profiles[name].get("model") else None
        self.slog.log("系统", "切换模型: %s (%s)" % (model, name))
        self._chat_write(RText("已切换: [bold]%s[/] (%s)" % (model, name), style="cyan"))

    # ---------- 输入处理 ----------
    def on_input_area_submitted(self, event: InputArea.Submitted) -> None:
        text = event.text.strip("\n").rstrip()
        if not text.strip():
            return
        if self.busy:
            self._chat_write(RText("[处理中, 请稍候]", style="yellow"))
            return
        # !shell模式(OpenCode !语义)
        if text.startswith("!") and len(text) > 1:
            self._run_shell_cmd(text[1:].strip())
            return
        cmd = text.strip()
        if self._handle_command(cmd):
            return
        # 单行斜杠命令但未匹配任何命令: 提示而不是发给LLM(OpenCode语义)
        if cmd.startswith("/") and "\n" not in cmd:
            self._chat_write(RText("未知命令: %s  (输入 / 查看命令列表)" % cmd, style="yellow"))
            return
        self._undo_stack = []   # 新发送清空redo栈
        text = A.expand_at_files(text)   # @文件附件展开
        self._chat_write(RPanel(RText(text), border_style="green", title="你"))
        self.slog.log("用户", text)
        self.messages.append({"role": "user", "content": text})
        self._cancel_event = threading.Event()   # 每轮新取消事件
        self._set_busy(True)
        threading.Thread(target=self._run_round, daemon=True).start()

    def _run_shell_cmd(self, cmdline):
        """!命令: PowerShell执行, 输出进会话(可让agent继续分析)。
        执行期间置busy: 防止shell输出append与下一轮run_agent并发修改messages(竞态损坏请求结构)"""
        self._chat_write(RText("$ " + cmdline, style="bold cyan"))
        self.slog.log("shell", cmdline)
        self._set_busy(True)

        def worker():
            try:
                out, err = A.run_shell(cmdline)
                result = out if out else (err or "(无输出)")
            except Exception as e:
                result = "执行失败: %s" % e
            self.call_from_thread(self._shell_done, cmdline, result)

        threading.Thread(target=worker, daemon=True).start()

    def _shell_done(self, cmdline, result):
        self._chat_write(RText(result[:6000], style="dim"))
        note = "[!shell输出] %s\n%s" % (cmdline, result[:8000])
        self.messages.append({"role": "user", "content": note})
        self.slog.log("shell输出", result[:8000])
        self._chat_write(RText("(输出已进入会话上下文, 可直接追问'分析这个结果')", style="dim"))
        self._set_busy(False)
        self._save_current_session()

    # ---------- 会话管理(OpenCode /new /sessions语义; 归档落盘跨启动可恢复) ----------
    def _archive_current(self):
        if len([m for m in self.messages if m.get("role") == "user"]) == 0:
            return
        self._save_current_session()      # 落盘(重启后 /sessions 仍可恢复)
        self.sessions.append({
            "id": len(self.sessions) + 1,
            "title": getattr(self, "session_title", "") or ("会话%d" % (len(self.sessions) + 1)),
            "messages": self.messages,
            "slog_path": self.slog.path,
        })

    def _new_session(self):
        self._archive_current()
        self.messages = [{"role": "system", "content": A.build_system_prompt(self.cfg)}]
        try:
            self.slog.close()
        except Exception:
            pass
        self.slog = A.SessionLog()
        self.session_title = ""
        self._titled = False
        self._undo_stack = []
        self._session_tool_log = []     # 台账重置
        self._live_clear()
        self._refresh_mode_label()
        self._chat_write(RText("已开始新会话(旧会话已归档, /sessions 可切回) · 日志: %s"
                                % os.path.basename(self.slog.path), style="dim"))

    def _open_sessions(self):
        entries = [("current", "▸ 当前会话: %s" % (getattr(self, "session_title", "") or "未命名"))]
        for s in self.sessions:
            n_msgs = len([m for m in s["messages"] if m.get("role") in ("user", "assistant")])
            entries.append(("m:%d" % s["id"], "%s. %s  [dim](%d条消息 · 本次运行内)[/]"
                            % (s["id"], s["title"], n_msgs)))
        for d in self._disk_sessions()[:20]:
            entries.append(("d:%s" % d["file"],
                            "↻ %s  [dim](%d条 · %s保存 · 重启可恢复)[/]"
                            % (d["title"], d["n_msgs"], d["saved_at"])))
        self.push_screen(GenericPickerScreen("会话列表", entries, "current"), self._on_session_picked)

    def _on_session_picked(self, sid):
        if not sid or sid == "current":
            return
        self._archive_current()
        if sid.startswith("d:"):
            fn = sid[2:]
            d = next((x for x in self._disk_sessions() if x["file"] == fn), None)
            if not d:
                self._chat_write(RText("会话文件读取失败: %s" % fn, style="red"))
                return
            self.messages = d["messages"]
            # 恢复的旧会话: 刷新system prompt(时间/扩展工具状态/站点列表以当前为准)
            if self.messages and self.messages[0].get("role") == "system":
                self.messages[0]["content"] = A.build_system_prompt(self.cfg)
            A.repair_messages(self.messages)   # 会话可能保存于工具执行中途, 清理尾部悬挂消息防下轮400
            try:
                self.slog.close()
            except Exception:
                pass
            self.slog = A.SessionLog(path=d["slog_path"]) if os.path.exists(d["slog_path"]) \
                else A.SessionLog()
            self.session_title = d["title"]
            self._titled = True
            self._undo_stack = []
            self._session_tool_log = []
            self._live_clear()
            self._refresh_mode_label()
            self._chat_write(RText("已恢复历史会话: %s (继续对话)" % d["title"], style="cyan"))
            self.slog.log("系统", "从磁盘恢复会话: %s" % d["title"])
            return
        sel_entry = next((s for s in self.sessions if ("m:%d" % s["id"]) == sid), None)
        if sel_entry:
            self.messages = sel_entry["messages"]
            self.slog.close()
            self.slog = A.SessionLog(path=sel_entry["slog_path"])
            self._titled = True
            self.session_title = sel_entry["title"]
            self._undo_stack = []
            self._session_tool_log = []
            self._live_clear()
            self._refresh_mode_label()
            self._chat_write(RText("已切回会话%d: %s (继续对话)" % (sel_entry["id"], sel_entry["title"]),
                                   style="cyan"))
            self.slog.log("系统", "切回会话: %s" % sel_entry["title"])

    # ---------- undo/redo(OpenCode /undo /redo语义) ----------
    def _cmd_undo(self):
        n_user = len([m for m in self.messages if m.get("role") == "user"])
        if n_user == 0:
            self._chat_write(RText("没有可撤销的消息", style="dim"))
            return
        popped = []
        while self.messages and self.messages[-1].get("role") != "user":
            popped.append(self.messages.pop())
        popped.append(self.messages.pop())   # 最后一条user消息
        self._undo_stack.append(popped)
        last_user_text = popped[-1].get("content") or ""
        self._chat_write(RPanel(RText(last_user_text[:2000]), border_style="yellow",
                                title="已撤销(输入框可编辑重发)"))
        self._chat_write(RText("已撤销最近一次提问及其回答 · /redo 恢复 · 撤销的原文已放回输入框", style="dim"))
        ia = self.query_one("#input", InputArea)
        ia.text = last_user_text
        ia.focus()

    def _cmd_redo(self):
        if not self._undo_stack:
            self._chat_write(RText("没有可恢复的消息", style="dim"))
            return
        popped = self._undo_stack.pop()
        self.messages.extend(reversed(popped))
        self._chat_write(RText("已恢复撤销的消息(%d条)" % len(popped), style="cyan"))

    # ---------- /btw 旁路提问 ----------
    def _cmd_btw(self, question):
        if not question:
            self._chat_write(RText("用法: /btw <问题> —— 旁路提问, 回答用当前上下文但不进入主对话", style="dim"))
            return
        self._chat_write(RText("[btw] 思考旁路问题...", style="dim"))

        def worker():
            try:
                msgs = list(self.messages) + [{"role": "user", "content":
                                              "[旁路提问, 不进入主上下文] " + question}]
                ans = A.llm_ask(self._effective_profile(), self.active, msgs, max_chars=5000)
                self.slog.log("btw", "问: %s\n答: %s" % (question, ans))
                self.call_from_thread(self._chat_write,
                                      RMarkdown("[btw·旁路回答·未进入主上下文]\n\n" + ans))
            except Exception as e:
                self.call_from_thread(self._chat_write,
                                      RText("[btw] 失败: %s" % e, style="red"))

        threading.Thread(target=worker, daemon=True).start()

    # ---------- 其他命令 ----------
    def _cmd_compact(self):
        n = A._compact_messages(self.messages, force=True)
        total = sum(len(str(m.get("content") or "")) for m in self.messages)
        self._chat_write(RText("上下文已压缩: %s · 当前约%s字符(预算%s)"
                               % (("%d处" % n) if n else "无需压缩", format(total, ","),
                                  format(A.CONTEXT_CHAR_BUDGET, ",")), style="cyan"))

    def _cmd_export(self, arg):
        try:
            path = A.export_session(self.messages, arg.strip() if arg else None)
        except Exception as e:
            self._chat_write(RText("导出失败: %s (检查路径/权限)" % e, style="red"))
            return
        self._chat_write(RText("会话已导出: %s" % path, style="cyan"))
        self.slog.log("系统", "导出会话: %s" % path)

    def _cmd_status(self):
        st = plugin_loader.status()
        n_msgs = len(self.messages)
        total = sum(len(str(m.get("content") or "")) for m in self.messages)
        eff = self._effective_profile()
        est_tokens, limit, pct = self._context_usage()
        self._chat_write(RMarkdown(
            "## 运行状态\n"
            "| 项 | 值 |\n|---|---|\n"
            "| 生效模型 | %s (%s) |\n| 模式 | %s |\n"
            "| 会话 | %d条消息 · 约%s字符(≈%s/%s token, %d%%) · 归档%d(内存)+磁盘可恢复 |\n"
            "| 工具 | 当前模式可用%d · 插件%s · MCP%s服务器(%s工具) |\n"
            "| 扩展加载 | %s |\n"
            "| 会话日志 | %s |"
            % (eff["model"], self.active,
               "只读查询" if self.mode == "query" else "可执行",
               n_msgs, format(total, ","), format(est_tokens, ","), format(limit, ","), pct,
               len(self.sessions),
               len(toolset(self.mode)), st["plugin_tools"],
               len(st["mcp_servers"]), st["mcp_tools"],
               "OK" if not plugin_loader.warnings() else ("⚠ " + "; ".join(plugin_loader.warnings())[:120]),
               os.path.basename(self.slog.path))))

    def _handle_command(self, cmd) -> bool:
        if cmd in ("/exit", "/quit", "exit"):
            self.action_quit()   # 与Ctrl+Q同路: 退出前保存会话
            return True
        if cmd == "/new":
            self._new_session()
            return True
        if cmd == "/sessions":
            self._open_sessions()
            return True
        if cmd == "/agents":
            self._open_agents()
            return True
        if cmd == "/undo":
            self._cmd_undo()
            return True
        if cmd == "/redo":
            self._cmd_redo()
            return True
        if cmd.startswith("/btw"):
            self._cmd_btw(cmd[4:].strip())
            return True
        if cmd == "/compact":
            self._cmd_compact()
            return True
        if cmd.startswith("/export"):
            self._cmd_export(cmd[7:].strip())
            return True
        if cmd == "/status":
            self._cmd_status()
            return True
        if cmd == "/copy":
            self._cmd_copy()
            return True
        if cmd == "/open":
            self._open_answer_in_editor()
            return True
        if cmd == "/help":
            self._chat_write(RMarkdown(
                "## 键位(OpenCode V2对齐)\n"
                "- **Enter** 发送 · **Shift/Ctrl/Alt+Enter** 或 **Ctrl+J** 换行 · **ESC** 关弹窗(运行中双击=中止)\n"
                "- **Tab / Shift+Tab** 切只读/可执行模式 · **F2** 循环最近模型 · **Ctrl+Q** 退出\n"
                "- **Ctrl+X 组合键**: N新会话 L会话列表 U撤销 R恢复 M模型 A模式\n"
                "- **Ctrl+P** 命令面板(全命令过滤选择)\n"
                "- **复制**: 会话区鼠标拖选 → **Ctrl+C**; 无选区按 **Ctrl+C** 弹复制菜单(最近回答/最近3轮/10轮/全部/工具证据); **Ctrl+O** 在系统编辑器打开; `/copy` 复制最近回答\n\n"
                "## 会话命令\n"
                "- `/new` 新会话(旧会话归档) · `/sessions` 切回历史会话(**已落盘, 重启后仍可恢复**)\n"
                "- `/undo` 撤销最近提问+回答(原文回输入框) · `/redo` 恢复\n"
                "- `/btw <问题>` 旁路提问(回答不进主上下文) · `/summary` 会话摘要\n"
                "- `/export [路径]` 导出会话为markdown · `/compact` 手动压缩上下文\n"
                "- `/models` 模型选择器(实时发现) · `/agents` 主agent模式选择器\n"
                "- `/status` 运行状态 · `/exit` 退出\n\n"
                "## 用法\n"
                "- 说人话即可: '今天13点以后某项目未处理的告警研判下' / 告警ID+项目 / 粘贴完整告警\n"
                "- `@文件路径` 附件嵌入(如 @payload.txt) · `@explore/@general/@rca 任务` 点名委派子代理 · `!命令` shell模式\n"
                "- 处置回写在可执行模式下仍会弹权限确认窗, 你点了'允许'才真正执行\n"
                "- 批量大时可说'派explore子代理去侦察', 单主题深挖说'派general子代理'\n"
                "- 左下转圈=思考中; ⚙行=工具调用(evidence限频约12s/次属正常)\n"
                "- 全部留痕在 logs/session-*.log; 防火墙封禁/加白任何模式都只给建议, 由你手动执行"))
            return True
        if cmd == "/summary":
            self._chat_write(RText("生成会话摘要中...", style="dim"))
            threading.Thread(target=self._gen_summary, daemon=True).start()
            return True
        if cmd == "/models":
            self.action_open_models()
            return True
        if cmd == "/model" or cmd.startswith("/model "):
            parts = cmd.split()
            if len(parts) == 1:
                self._chat_write(RText("当前: [%s] %s" % (self.active, self.profiles[self.active].get("model")),
                                       style="bold"))
                self._chat_write(RText("可用: " + ", ".join(
                    "[%s] %s" % (k, v.get("model")) for k, v in self.profiles.items())))
            else:
                name = parts[1]
                if name in self.profiles:
                    self.active = name
                    self._chat_write(RText("已切换到 [%s] %s" % (name, self.profiles[name].get("model")),
                                           style="bold"))
                else:
                    self._chat_write(RText("未知profile %r, 可用: %s" % (name, ", ".join(self.profiles)),
                                           style="red"))
            return True
        return False

    # ---------- 研判轮次(工作线程) ----------
    def _run_round(self):
        ui = TuiUI(self)
        try:
            answer, n_tools, n_think, dt = A.run_agent(
                self._effective_profile(), self.active, self.messages, self.slog, ui,
                tools_list=toolset(self.mode), allow_exec=(self.mode == "execute"),
                cancel=self._cancel_event, session_tool_log=self._session_tool_log)
            self.call_from_thread(self._round_done, dt, n_tools, n_think)
        except A.RoundCancelled:
            self.call_from_thread(self._round_cancelled)
        except Exception as e:
            self.call_from_thread(self._round_error, "%s: %s" % (type(e).__name__, e))

    def _round_done(self, dt, n_tools, n_think):
        self._chat_write(RRule("用时 %.1fs · 工具%d次 · 思考%s字" % (
            dt, n_tools, format(n_think, ",")), style="dim"))
        self._live_clear()
        self._set_busy(False)
        self._refresh_mode_label()   # 刷新上下文占用百分比
        self._save_current_session()  # 每轮结束落盘(崩溃/退出后 /sessions 仍可恢复)
        # 隐藏系统agent: 会话标题(首轮后自动生成, 对齐OpenCode title agent)
        if not getattr(self, "_titled", False) and len(self.messages) > 2:
            self._titled = True
            threading.Thread(target=self._gen_title, daemon=True).start()

    def _gen_title(self):
        """隐藏title agent: 后台一次性LLM生成会话短标题"""
        try:
            first_user = next((m.get("content") for m in self.messages
                               if m.get("role") == "user"), "") or ""
            first_ans = next((m.get("content") for m in self.messages
                              if m.get("role") == "assistant" and m.get("content")), "") or ""
            title = A.llm_oneshot(
                self._effective_profile(), self.active,
                "你是会话标题生成器。为下面的安全运营会话生成一个简短标题(不超过12个字),"
                "只输出标题本身, 不要引号不要标点。",
                "会话内容:\n%s\n\n研判结论节选:\n%s" % (str(first_user)[:800], str(first_ans)[:800]),
                max_chars=80)
            title = (title.strip().strip('"').strip("'").splitlines() or [""])[0][:16]
            if title:
                self.slog.log("会话标题", title)
                self.call_from_thread(self._set_title, title)
        except Exception:
            pass

    def _set_title(self, title):
        self.session_title = title
        self._refresh_mode_label()

    def _gen_summary(self):
        """隐藏summary agent: 一次性LLM生成会话摘要"""
        try:
            convo = []
            for m in self.messages:
                if m.get("role") in ("user", "assistant") and m.get("content"):
                    convo.append(("用户" if m["role"] == "user" else "研判") + ": "
                                 + str(m["content"])[:1200])
            text = A.llm_oneshot(
                self._effective_profile(), self.active,
                "你是会话摘要器。总结这次安全运营会话: ①研判的告警与定性结论(带证据等级)"
                "②关键证据链 ③待办事项。300字内, 用markdown。",
                "\n\n".join(convo[-40:]), max_chars=2500)
            self.slog.log("会话摘要", text)
            self.call_from_thread(self._chat_write, RMarkdown(text))
        except Exception as e:
            self.call_from_thread(self._chat_write,
                                  RText("摘要生成失败: %s" % e, style="red"))

    def _round_error(self, errtxt):
        self._chat_write(RText("系统错误: %s" % errtxt, style="red"))
        self._live_clear()
        self._set_busy(False)

    def _round_cancelled(self):
        self._chat_write(RText("⏹ 本轮已中止(上下文保留, 可继续追问或 /new)", style="yellow"))
        self._live_clear()
        self._set_busy(False)
        self._save_current_session()   # 中止也是有效状态, 落盘

    def _live_clear(self):
        self.query_one("#spinner", LoadingIndicator).display = False
        self.query_one("#thinkline", Static).update("")
        self.query_one("#thinktail", Static).display = False
        self.query_one("#livemd").display = False

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.query_one("#input", InputArea).disabled = busy
        self._refresh_mode_label()   # ⏳运行中标记实时显示
        if not busy:
            self.query_one("#input", InputArea).focus()

    def action_quit(self) -> None:
        self._save_current_session()   # 退出前保存(重启后 /sessions 可恢复)
        self.exit()

    def on_unmount(self) -> None:
        self._save_current_session()
        plugin_loader.shutdown()
        try:
            self.slog.close()
        except Exception:
            pass


def main():
    cfg = A.load_config()
    profiles, active = A.normalize_llm(cfg)
    A.configure(cfg)
    A.set_runtime(cfg, profiles)
    plugin_loader.bootstrap(cfg)
    app = SecOpsApp(cfg, profiles, active)
    from tools.permissions import set_confirm
    set_confirm(app._confirm_bridge)   # TUI弹窗确认
    app.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n退出。")
