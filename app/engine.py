# -*- coding: utf-8 -*-
"""EZcode Agent 引擎 (全新重写)
设计原则:
- 事件单通道: 所有引擎事件经 emit(dict) 一条路出去, 由宿主(桌面客户端)决定渲染
- 工具层复用 tools/ (配置驱动注册: 系统API/MCP/插件/内置分析脚本)
- 子代理: fresh context + steps上限 + 只读 + 进度心跳(5s节流)实时透传
- 权限: 写操作弹确认(emit confirm), 120s无响应自动拒绝
- 验证门: verify.py 确定性重算, 不采信模型自评
- 上下文: 超预算自动压缩历史工具结果(确定性)
"""
import json
import os
import re
import shutil
import sys
import time
import datetime
import platform
import threading
import concurrent.futures
import contextlib

if getattr(sys, "frozen", False):
    BASE_DIR = ROOT = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))      # app/
    ROOT = os.path.dirname(BASE_DIR)                           # EZcode 项目根目录
sys.path.insert(0, ROOT)

import requests                                          # noqa: E402
import verify                                            # noqa: E402
import llm as L                                          # noqa: E402
import store                                             # noqa: E402
from tools import TOOLS, HANDLERS, EXEC_TOOL_NAMES, configure, toolset  # noqa: E402
from tools import rag as _rag_mod, custom_api as _api_mod, analysis as _ana_mod  # noqa: E402
from tools import apply_builtin_tools as _apply_builtin  # noqa: E402
from tools import adapters as _ad_mod, secops as _secops_mod  # noqa: E402
from tools import office as _office_mod  # noqa: E402
from tools import todo as _todo_mod  # noqa: E402
import tools.permissions as perms                         # noqa: E402
import plugin_loader                                       # noqa: E402
import skills_lib                                          # noqa: E402

MAX_ROUNDS = 40              # 单轮 LLM 往返上限
TOOL_RESULT_LIMIT = 24000     # 工具结果进上下文的字符上限
CTX_BUDGET = 150000           # 上下文字符预算
CONFIRM_TIMEOUT = 600        # 权限确认超时(秒); 后台任务的确认也会立即弹窗, 长超时仅作兜底
TURN_BUDGET = 300             # 单轮总时长预算(秒): 到点强制基于已有信息收尾, 不许无限转
SUBAGENT_BUDGET = 120         # 单个子代理时长预算(秒); 子代理是主轮的一部分, 会吃掉主轮预算


class Cancelled(Exception):
    pass


def _warn_boot(text):
    """引擎启动期非致命告警(如MCP失败)——无控制台时只吞进stderr, 不阻塞启动。"""
    try:
        sys.stderr.write("[boot] %s\n" % text)
    except Exception:
        pass


# ---------------- 配置 ----------------

def load_config():
    path = cfg_path()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return _normalize(json.load(f))
    raise SystemExit("缺少接入配置 devices.json")


def _normalize(raw):
    cfg = dict(raw)
    ngsoc = raw.get("ngsoc") or {}
    if isinstance(ngsoc, dict):
        if isinstance(ngsoc.get("sites"), list):
            cfg["ngsoc_sites"] = ngsoc["sites"]
        if ngsoc.get("sites_file"):
            cfg["ngsoc_sites_path"] = ngsoc["sites_file"]
        if ngsoc.get("verify_ssl") is not None:
            cfg["ngsoc_verify_ssl"] = bool(ngsoc["verify_ssl"])
    nb = raw.get("netbox") or {}
    if isinstance(nb, dict):
        if nb.get("base_url"):
            cfg["netbox_base"] = nb["base_url"]
        if nb.get("token"):
            cfg["netbox_token"] = nb["token"]
        if nb.get("verify_ssl") is not None:
            cfg["netbox_verify_ssl"] = bool(nb["verify_ssl"])
    edr = raw.get("edr") or {}
    if isinstance(edr, dict):
        if isinstance(edr.get("projects"), list):
            cfg["edr_projects"] = {p["name"]: (p["server"], p["token"])
                                   for p in edr["projects"] if p.get("name")}
        if edr.get("client_module_path"):
            cfg["edr_client_path"] = edr["client_module_path"]
    cfg["projects"] = _ad_mod.build_projects(cfg)   # 项目=设备组(显式配置优先, 否则旧站点/项目内存迁移)
    conn = raw.get("connectors")
    cfg["connectors"] = conn if isinstance(conn, dict) else {}
    return cfg


def _ui_lang(cfg):
    """界面/回复语言(zh|en), 存在 devices.json 的 ui.lang; 缺省中文。
    用配置而不是环境变量: 语言要和界面同一个事实源, 重装后仍跟着配置走。"""
    ui = (cfg or {}).get("ui")
    return "en" if isinstance(ui, dict) and str(ui.get("lang") or "").lower().startswith("en") else "zh"


def _ui_persona(cfg):
    """上次使用的专家(devices.json 的 ui.persona), 启动即恢复;
    缺失、不认识或自建专家已被删时回落安全运营——不因为一条偏好起不来。
    校验用运行期注册表 PERSONAS(函数体在调用时才求值, 不受定义顺序影响)。"""
    ui = (cfg or {}).get("ui")
    pid = str(ui.get("persona") or "").strip().lower() if isinstance(ui, dict) else ""
    return pid if pid in PERSONAS else "ezcode"


def _ui_notify(cfg):
    """任务完成时的桌面提示: auto(仅不在看这个任务时) | always | off。
    默认 auto —— 你正盯着这个会话时不该再弹一个窗打断你。"""
    ui = (cfg or {}).get("ui")
    v = str(ui.get("notify") or "").strip().lower() if isinstance(ui, dict) else ""
    return v if v in ("auto", "always", "off") else "auto"


def llm_profiles(cfg):
    l = cfg.get("llm")
    if l and l.get("profiles"):
        profiles = {k: v for k, v in l["profiles"].items() if isinstance(v, dict)}
        active = l.get("active")
        if active not in profiles:
            active = next(iter(profiles))
        return profiles, active
    return {"deepseek": {"base_url": "https://api.deepseek.com",
                         "api_key": "", "model": "deepseek-chat"}}, "deepseek"


# ---------------- 专家(persona)中心 ----------------
# 会话=任务, persona=专家(布局对齐主流 Agent 工作台的「任务+专家」模型):
# - 内置两位专家: 安全运营(全量工具+研判提示词) / 通用编码(通用工具, 安全运营接口隐藏)
# - 自建专家: markdown 文件 + frontmatter 声明, 支持项目级 <ROOT>/agents/personas/
#   与全局 ~/.ezcode/personas/ 两级目录, 同名项目级优先;
#   frontmatter: label/icon/description/tools(secops|general)/welcome, 正文即系统提示词
_BUILTIN_PERSONAS = {
    "ezcode": {"label": "安全运营", "icon": "🛡", "secops": True,
               "desc": "告警研判、事件调查、资产与威胁情报联动的安全运营专家",
               "prompt": "prompts/system.md",
               "welcome": "粘贴告警或直接说人话, EZcode 自动调用已接入的系统能力、跑行为链、"
                          "检索判据库给出研判",
               "cards": [
                   {"icon": "🛡", "title": "告警研判", "sub": "批量分诊 · 行为链 · 处置建议",
                    "prompt": "帮我研判今天未处理的高危告警，先做批量分诊再给处置建议"},
                   {"icon": "📚", "title": "查知识库", "sub": "判据 · 历史处置 · 规范",
                    "prompt": "检索知识库，总结这类事件的处置判据与历史案例"},
                   {"icon": "🔎", "title": "事件调查", "sub": "资产核查 · 情报比对 · 证据链",
                    "prompt": "对这条告警做完整调查：资产归属、威胁情报比对、给出结论与置信度"},
                   {"icon": "🧪", "title": "分析脚本", "sub": "批量解析 · 统计 · 格式转换",
                    "prompt": "帮我写一个 Python 脚本统计日志并输出报表"},
               ]},
    "coder": {"label": "通用编码", "icon": "💻",
              "desc": "写代码、改文件、跑命令、分析数据的通用桌面助理",
              "prompt": "prompts/coding.md",
              "welcome": "把任务交给 EZcode: 写代码、改文件、跑命令、分析数据; "
                         "只读模式可直接干, 写文件/跑命令请先切到可执行模式",
              "cards": [
                  {"icon": "💻", "title": "写代码 / 跑命令", "sub": "脚本 · 构建 · 自动化",
                   "prompt": "帮我写一个 Python 脚本统计日志并输出报表"},
                  {"icon": "📁", "title": "批量改文件", "sub": "检索 · 精确编辑 · 验证",
                   "prompt": "在这个项目里找出所有 TODO 注释，列出来并给处理建议"},
                  {"icon": "🧪", "title": "数据分析", "sub": "解析 · 统计 · 可视化",
                   "prompt": "帮我分析这份数据文件的字段分布和异常点"},
                  {"icon": "🌐", "title": "查公开资料", "sub": "搜索 · 文档 · 技术方案",
                   "prompt": "搜一下这个报错的常见原因和解决方案"},
              ]},
    "assistant": {"label": "日常工作助手", "icon": "🧑‍💼",
                  "desc": "写作润色、总结提炼、翻译、查资料、待办管理的日常办公助手",
                  "prompt": "prompts/assistant.md",
                  "welcome": "写作、总结、翻译、查资料、记待办都可以交给我; "
                             "试着说「记一下: 明天上午开例会」体验本地待办",
                  "cards": [
                      {"icon": "📝", "title": "写作 / 润色", "sub": "邮件 · 周报 · 方案",
                       "prompt": "帮我写一份本周工作周报的框架，我口述要点你来组织"},
                      {"icon": "✅", "title": "记待办", "sub": "记任务 · 列清单 · 标完成",
                       "prompt": "记一下待办：明天上午找张工对策略，周五前交周报"},
                      {"icon": "🌐", "title": "查资料", "sub": "搜索 · 整理 · 摘要",
                       "prompt": "帮我查一下这个概念的公开资料，整理成一页速览"},
                      {"icon": "🌍", "title": "翻译 / 总结", "sub": "中英互译 · 长文提炼",
                       "prompt": "把这段英文资料翻译成中文并总结成三个要点"},
                  ]},
}


def _persona_dirs():
    return (("project", os.path.join(ROOT, "agents", "personas")),
            ("global", os.path.join(os.path.expanduser("~"), ".ezcode", "personas")))


def _parse_md_meta(text):
    """frontmatter(key: value 单行) → (meta, body); 无 frontmatter 时 body=全文。"""
    meta, body = {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"').strip("'")
        body = m.group(2)
    return meta, body


PERSONAS = {}          # 运行期专家注册表: 内置 + 自建


def _generic_cards(label=""):
    """自建专家的兜底欢迎卡片(未配 cards 时)。"""
    return [
        {"icon": "💬", "title": "直接下任务", "sub": "按专家提示词工作",
         "prompt": "你好，先介绍一下你能帮我做什么、擅长什么"},
        {"icon": "📝", "title": "写与总结", "sub": "起草 · 润色 · 提炼",
         "prompt": "帮我把一段材料总结成要点"},
        {"icon": "🌐", "title": "查资料", "sub": "搜索 · 整理 · 摘要",
         "prompt": "帮我查一个概念并整理成一页速览"},
        {"icon": "✅", "title": "记待办", "sub": "本地清单 · 跨会话",
         "prompt": "记一下待办："},
    ]


def _reload_personas():
    PERSONAS.clear()
    for k, v in _BUILTIN_PERSONAS.items():
        PERSONAS[k] = dict(v, builtin=True)
    for scope, d in _persona_dirs():
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".md") or fn.startswith("_"):
                continue
            pid = fn[:-3]
            if pid in PERSONAS:          # 项目级先加载, 同名项目级优先
                continue
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    text = f.read()
                meta, body = _parse_md_meta(text)
            except Exception:
                continue
            if not body.strip():
                continue
            PERSONAS[pid] = {
                "label": meta.get("label") or pid,
                "icon": meta.get("icon") or "🧠",
                "desc": meta.get("description") or "自建专家",
                "welcome": meta.get("welcome") or "把任务交给这位专家，我将按专家提示词工作。",
                "secops": str(meta.get("tools") or "").strip().lower() == "secops",
                "prompt_text": body.strip(),
                "scope": scope, "file": fn, "custom": True,
            }


_reload_personas()


def cfg_path():
    """当前实际使用的配置文件路径(devices.json 优先)。"""
    for name in ("devices.json", "config.json"):
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            return p
    return os.path.join(ROOT, "devices.json")


def tool_call_key(tc):
    """把一次工具调用归一化成可比较的键, 用于识别"换个参数壳子但拿到同样结果"的空转。

    doc_read 特别忽略 max_chars: 大文档场景下模型会连续用
    30000/130000/250000/320000/400000 反复重读同一个开头, 每次都被引擎截成
    同样的 24000 字 —— 这是"跑 1275 秒没有结果"的主循环。
    """
    try:
        a = json.loads(tc["function"].get("arguments") or "{}")
    except Exception:
        a = {}
    name = tc["function"]["name"]
    if name == "doc_read":
        # outline 也要进键: 否则"先看章节索引 → 再读第一段"会被误判为重复调用拦掉
        return (name, str(a.get("path") or ""),
                str(a.get("offset") or 0), str(a.get("find") or ""),
                "outline" if a.get("outline") else "")
    return (name, json.dumps(a, sort_keys=True, ensure_ascii=False, default=str))


def _capability_manifest(persona, mode="query"):
    """按能力分组生成"当前接入的设备能力"清单(设备无关, 注册即出现)。
    提示词不再点名工具; 接入新安全设备=注册工具+caps.py打标签, 此清单自动更新。

    mode 必须跟本轮实际下发到模型的 toolset() 一致: 以前写死 "execute", 只读模式下
    提示词宣称 shell/run_python 可用、而函数 schema 根本没有, 模型会为此反复试探、
    派子代理绕路、甚至拿 b64_decode 探测工具名 —— 一次问答能空转二十分钟。"""
    from tools import CAPABILITIES, CAPABILITY_GROUPS, toolset
    secops = bool((PERSONAS.get(persona) or {}).get("secops"))
    avail = {t["function"]["name"] for t in toolset(mode, persona, secops=secops)}
    lines = []
    for label, cap in CAPABILITY_GROUPS:
        names = sorted(n for n, cs in CAPABILITIES.items()
                       if cap in cs and n in avail)
        if names:
            lines.append("- %s: %s" % (label, ", ".join(names)))
    return lines


def build_system_prompt(cfg, persona="ezcode", mode="query"):
    """系统提示词。mode 决定能力清单与只读说明, 必须与实际工具面一致。"""
    spec = PERSONAS.get(persona) or PERSONAS["ezcode"]
    if spec.get("prompt_text"):
        prompt = spec["prompt_text"]          # 自建专家: 提示词内联于 markdown 正文
    else:
        path = os.path.join(ROOT, spec["prompt"])
        with open(path, encoding="utf-8") as f:
            prompt = f.read()
    lines = ["", "# 环境信息(动态注入)",
             "当前时间: %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
             "回复语言: %s" % ("English — 与界面语言一致; 告警字段/设备名/命令等专有名词保留原文"
                              if _ui_lang(cfg) == "en" else "简体中文")]
    if spec.get("secops"):
        from tools import tools_with
        caps_lines = _capability_manifest(persona, mode)
        if caps_lines:
            lines.append("当前接入的研判能力(按需选择; 具体参数/限频/批量技巧看各工具描述):")
            lines.extend(caps_lines)
        intel = tools_with("intel")
        lines.append("威胁情报: %s" % (
            "已接入(%s)" % ", ".join(intel) if intel
            else "未接入, 外网源定性时标注'情报待人工查询'"))
        sites = []
        sp = cfg.get("ngsoc_sites_path")
        if sp and os.path.exists(sp):
            try:
                with open(sp, encoding="utf-8") as f:
                    sites = json.load(f).get("sites", [])
            except Exception:
                pass
        if not sites and isinstance(cfg.get("ngsoc_sites"), list):
            sites = cfg["ngsoc_sites"]
        if sites:
            lines.append("告警检索站点: " + ", ".join(
                "%s(%s,%s)" % (s["name"], s.get("code"),
                               "新API" if s.get("apiStyle") == "new" else "旧API")
                for s in sites) + ", all=全站检索")
        else:
            lines.append("告警检索: 未配置站点, 检索类工具不可用")
        lines.append("主机侧告警项目: %s"
                     % (", ".join((cfg.get("edr_projects") or {}).keys()) or "未登记"))
    else:
        caps_lines = _capability_manifest(persona, mode)
        if caps_lines:
            lines.append("当前可用的工具能力(实时状态; 与角色提示词中的工具示例冲突时以本清单为准):")
            lines.extend(caps_lines)
        else:
            lines.append("当前未启用任何工具能力(全部分组已在设置中心关闭)。")
        if mode != "execute":
            lines.append("当前为只读查询模式: shell / run_python / file_write / file_edit / office_edit "
                         "**不在可调用工具清单里**, 不要尝试调用它们, 也不要用子代理绕开"
                         "(子代理同样是只读工具面)。需要写文件或执行命令时, 请用户切换到可执行模式; "
                         "若现有只读工具已经够回答问题, 直接作答。")
        lines.append("操作系统: Windows (%s)" % platform.platform())
        lines.append("工作目录: %s" % os.path.expanduser("~"))
        try:
            lines.append(_office_mod.detect_summary())
        except Exception:
            pass
    # 连接器开关: 技能/插件被取消勾选时不再注入提示词
    _conn = cfg.get("connectors") if isinstance(cfg.get("connectors"), dict) else {}
    # 用户/项目自定义技能(全局+项目级 .md), 启用项注入——设置中心可管理
    try:
        skills_text = "" if _conn.get("skills") is False else skills_lib.prompt_block()
        if skills_text:
            lines.append(skills_text)
    except Exception:
        pass
    # 插件的附加提示词(PLUGIN_PROMPT: 运营人员写进插件的使用思路/纪律)
    # 旧CLI有、桌面版此前漏接的断层——不接则插件只有工具没有"何时用"的说明
    try:
        addon = "" if _conn.get("plugins") is False else plugin_loader.prompt_addon()
        if addon:
            lines.extend(["", "# 插件附加说明", addon])
    except Exception:
        pass
    # 收敛纪律(所有专家通用): 大文档/大结果场景下, 模型容易把"答一个问题"
    # 膨胀成"把全文完整提取出来", 反复重读并层层派生子代理, 最后不给结论。
    lines.extend([
        "", "# 回答纪律(所有任务通用)",
        "- 读大文档(Word/Excel/PPT/PDF)走三步: doc_read(outline=true) 拿章节索引 → "
        "fs_grep 或 doc_read(find='关键词') 定位 → doc_read(offset=<pos>) 精读命中处。"
        "fs_grep 已能直接检索 docx/xlsx/pptx/pdf 内部文字并返回 pos(可直接当 offset 用), "
        "不要用 file_read 读压缩包, 也不要把整份文档分批读一遍。",
        "- 工具结果带 next_offset 或被截断时, 按 hint 用 offset 翻页、用 find 关键词定位; "
        "不要用更大的 max_chars 反复重读同一段, 那只会拿到同样的开头。",
        "- 一旦已取得足以回答用户问题的信息, 立刻给结论(先答怎么配置/怎么用, 再补细节)。"
        "不要为了'完整提取全文'无限扩大任务范围, 也不要反复委派子代理做同一件事。",
        "- 单轮有时间预算; 预算将尽时会要求收尾, 此时基于已有信息给出可执行的答案, "
        "并明确列出哪些部分尚未获取或未核实, 不要沉默地继续跑。",
    ])
    return "\n".join([prompt] + lines)


# ---------------- 上下文/消息 ----------------

def total_chars(messages):
    return sum(len(str(m.get("content") or "")) for m in messages)


def compact_messages(messages, force=False):
    """超预算压缩历史工具结果(确定性, 保最近轮完整)。返回压缩条数。"""
    if not force and total_chars(messages) <= CTX_BUDGET:
        return 0
    mark = "...[已压缩:历史工具结果]"
    n = 0
    for m in messages:
        if total_chars(messages) <= CTX_BUDGET:
            break
        if m.get("role") == "tool" and mark not in (m.get("content") or "") \
                and len(m.get("content") or "") > 600:
            m["content"] = m["content"][:400] + " " + mark
            n += 1
    for m in messages:
        if total_chars(messages) <= CTX_BUDGET:
            break
        if m.get("role") == "assistant" and len(m.get("content") or "") > 4000:
            m["content"] = m["content"][:800] + " ...[已压缩:历史回答]"
            n += 1
    return n


_GATE_MARK = "── 流程校验(确定性, 非模型自评) ──"


def strip_gate_banner(content):
    """剥离旧版本写入assistant正文的校验横幅。
    横幅属于系统旁注, 不是模型结论; 留在对话历史里会让下一轮模型误以为
    '自己上一轮给过封禁建议且没通过校验', 从而追着补情报/提封禁。"""
    if not isinstance(content, str):
        return content
    cut = content.find(_GATE_MARK)
    return content[:cut].rstrip() if cut >= 0 else content


def repair_messages(messages):
    """移除尾部悬挂的tool结果/无响应的assistant(tool_calls), 防下轮400;
    顺带清洗历史里残留的校验横幅(兼容旧会话)。"""
    for m in messages:
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            m["content"] = strip_gate_banner(m["content"])
    while messages and messages[-1].get("role") == "tool":
        messages.pop()
    if messages and messages[-1].get("role") == "assistant" \
            and messages[-1].get("tool_calls"):
        messages.pop()


def expand_at_files(text, subagent_names, max_chars=30000):
    """@路径 附件展开(读前过file_read权限门); @explore 等子代理点名不当作附件。"""
    def _repl(m):
        raw = m.group(1)
        # 输入框「＋」选择的文件名可能含空格, 用 @"C:\path with space\x.log" 形式引用
        if len(raw) >= 2 and raw[0] in ("\"", "'") and raw[-1] == raw[0]:
            raw = raw[1:-1]
        if raw.lower() in {k.lower() for k in subagent_names}:
            return m.group(0)
        path = os.path.expandvars(os.path.expanduser(raw))
        reason, ok = perms.gate("file_read", path, "附件读取: %s" % path)
        if not ok:
            return "[附件 %s 读取被拒绝: %s]" % (raw, reason)
        try:
            content = open(path, encoding="utf-8", errors="replace").read()
        except Exception as e:
            return "[附件 %s 读取失败: %s]" % (raw, e)
        if len(content) > max_chars:
            content = content[:max_chars] + " ...[附件过大已截断]"
        return "[附件 %s 内容如下]\n```\n%s\n```" % (raw, content)
    return re.sub(r"(?<![\w@])@(\"[^\"]+\"|'[^']+'|[^\s@,;，；]+)", _repl, text)


# ---------------- 会话日志(证据链留痕) ----------------

class Slog:
    def __init__(self, path=None):
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        self.path = path or os.path.join(
            ROOT, "logs", "session-%s.log" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        self._fh = None
        self._lock = threading.Lock()

    def log(self, kind, text):
        with self._lock:
            if self._fh is None:
                self._fh = open(self.path, "a", encoding="utf-8")
            ts = datetime.datetime.now().strftime("%H:%M:%S")
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


# ---------------- 子代理注册 ----------------

SUBAGENTS = {
    "explore": {"description": "只读侦察, 批量分诊出简报", "steps": 8},
    "general": {"description": "深度调查, 完整证据报告", "steps": 16},
    "rca": {"description": "失陷告警四步根因分析", "steps": 12},
}


def _load_custom_agents():
    d = os.path.join(ROOT, "agents")
    if not os.path.isdir(d):
        return
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".md"):
            continue
        name = fn[:-3]
        try:
            text = open(os.path.join(d, fn), encoding="utf-8").read()
            meta, body = {}, text
            m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
            if m:
                for line in m.group(1).splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip().strip('"').strip("'")
                body = m.group(2)
            SUBAGENTS[name] = {
                "description": meta.get("description") or ("自定义子代理 " + name),
                "steps": max(2, int(meta.get("steps") or 8)),
                "prompt_suffix": "\n# 自定义角色: %s\n%s\n" % (name, body.strip()[:3000]),
            }
        except Exception:
            continue


_load_custom_agents()


def subagent_schema():
    return {"type": "function", "function": {
        "name": "subagent",
        "description": "委派子代理在全新上下文中执行任务(主上下文不被污染)。任务必须自包含"
                       "(写清站点/时间/IP/告警要素)。多个独立调查任务请在本批tool_calls里同时"
                       "发起多个subagent调用——引擎自动并行执行(≤4并发)。可用: "
                       + "; ".join("%s: %s (steps≤%d)" % (k, v["description"][:40], v["steps"])
                                   for k, v in SUBAGENTS.items()),
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "description": "自包含的任务描述"},
            "agent": {"type": "string", "enum": sorted(SUBAGENTS.keys())}},
            "required": ["task", "agent"]}}}


# ---------------- 引擎 ----------------

def _session_property(name):
    """会话级字段代理: 读写自动落到"当前线程绑定的会话 ctx"。
    后台轮次绑定自己的 ctx, js_api 主线程落到激活会话 —— 于是同一个 Engine
    能同时跑多个互不干扰的任务, 而原来那 100 多处 self.sess/self.busy 代码不用改。"""
    def _get(self):
        return self._ctx()[name]

    def _set(self, value):
        self._ctx()[name] = value

    return property(_get, _set)


class Engine:
    """Agent 引擎。emit(dict) 由宿主注入; 要求可被任意线程调用。
    多任务: 一个 Engine 内部维护多个会话 ctx, 每个 ctx 持有独立的
    messages / 专家 / 模型 / 模式 / 运行状态 / cancel; emit 事件带 sid,
    前端只渲染激活会话的事件, 后台会话继续跑但不会串台。"""

    _SESSION_FIELDS = ("sess", "slog", "sid", "busy", "cancel",
                       "persona", "mode", "active", "system_prompt")
    sess = _session_property("sess")
    slog = _session_property("slog")
    sid = _session_property("sid")
    busy = _session_property("busy")
    cancel = _session_property("cancel")
    persona = _session_property("persona")
    mode = _session_property("mode")
    active = _session_property("active")
    system_prompt = _session_property("system_prompt")

    def __init__(self, emit, cfg=None):
        self.emit = emit
        self.cfg = cfg or load_config()
        configure(self.cfg)
        # 初始化期(还没建第一个会话)的赋值先落在 _boot, _new_session 会把它带进首个 ctx
        self._ctxs = {}
        self._active_sid = None
        self._tls = threading.local()
        self._boot = dict.fromkeys(self._SESSION_FIELDS)
        self._boot["sid"] = ""
        self.profiles, self.active = llm_profiles(self.cfg)
        self.persona = _ui_persona(self.cfg)   # 新建任务的默认专家(上次退出时那位)
        self.lang = _ui_lang(self.cfg)     # zh|en: 界面+回复语言, 全局
        self.mode = "query"                # query=只读 / execute=可执行(写操作逐次确认)
        self.system_prompt = build_system_prompt(self.cfg, self.persona, self.mode)
        self.busy = False
        self.cancel = threading.Event()
        self._confirm_seq = 0
        self._confirm_waiters = {}          # seq -> (Event, box); seq全局唯一, 可跨会话路由
        self._lock = threading.RLock()
        perms.set_confirm(self._confirm_gate)
        # "本会话总是允许"按任务隔离: 授权落在当前线程绑定的会话 sid 上
        perms.set_scope(lambda: self._ctx().get("sid"))
        self._new_session()
        # MCP接入: 旧CLI有、桌面客户端引擎漏接的断层——不接则devices.json的
        # mcp.servers在客户端里始终不生效。bootstrap幂等且MCP在后台线程连接, 不阻塞启动。
        try:
            plugin_loader.bootstrap(self.cfg)
        except Exception as e:
            _warn_boot("MCP加载失败: %s" % e)

    # ---------- 属性/状态 ----------

    # ---------- 多任务: 会话上下文解析 ----------

    def _ctx(self):
        """当前线程绑定的会话; 未绑定(js_api主线程)回落到激活会话。"""
        c = getattr(self._tls, "ctx", None)
        if c is not None:
            return c
        if self._active_sid and self._active_sid in self._ctxs:
            return self._ctxs[self._active_sid]
        return self._boot

    def _ctx_for(self, sid=None):
        return self._ctxs.get(str(sid)) or self._ctx() if sid else self._ctx()

    @contextlib.contextmanager
    def _bound(self, ctx):
        """在指定会话上下文里执行一段代码(切会话/遍历全部会话时用)。"""
        prev = getattr(self._tls, "ctx", None)
        self._tls.ctx = ctx
        try:
            yield
        finally:
            self._tls.ctx = prev

    @property
    def profile(self):
        return dict(self.profiles.get(self.active) or {})

    def state(self):
        with self._lock:
            spec = PERSONAS.get(self.persona) or PERSONAS["ezcode"]
            return {"mode": self.mode, "model": self.profile.get("model"),
                    "active": self.active, "busy": self.busy, "sid": self.sid,
                    "lang": self.lang,
                    "title": self.sess["title"], "ctx_pct": self.ctx_pct(),
                    "n_msgs": len(self.sess["messages"]),
                    "persona": self.persona,
                    "persona_label": spec["label"],
                    "persona_icon": spec.get("icon", "🧠"),
                    "persona_welcome": spec.get("welcome", ""),
                    "persona_cards": self.persona_cards(),
                    "notify": _ui_notify(self.cfg),
                    "profiles": {k: v.get("model") for k, v in self.profiles.items()}}

    def ctx_pct(self):
        limit = int(self.profile.get("context") or 128000)
        est = int(total_chars(self.sess["messages"]) * 0.55)
        return min(100, est * 100 // max(limit, 1))

    # ---------- 会话 ----------

    def _make_ctx(self, sid, sess, slog, **overrides):
        """以 _boot 为模板建一份会话状态: 每个任务独立的 messages/专家/模型/模式/运行状态。"""
        ctx = {k: self._boot.get(k) for k in self._SESSION_FIELDS}
        ctx.update({"sid": sid, "sess": sess, "slog": slog,
                    "busy": False, "cancel": threading.Event()})
        ctx.update(overrides)
        self._ctxs[sid] = ctx
        return ctx

    def _new_session(self):
        sid = store.new_id()
        slog = Slog()
        # 新任务沿用"当前任务"的专家/模型/模式作为起点(之后各自独立, 可单独切换);
        # 首个任务用启动时的默认专家。用户的每次选择都会被下一个新任务记住。
        src = self._ctx() if self._active_sid else self._boot
        prompt = src.get("system_prompt") or self._boot["system_prompt"]
        sess = {"messages": [{"role": "system", "content": prompt}],
                "title": "", "tool_log": [], "slog_path": slog.path}
        self._make_ctx(sid, sess, slog,
                       persona=src.get("persona") or self._boot["persona"],
                       mode=src.get("mode") or "query",
                       active=src.get("active") or self._boot["active"],
                       system_prompt=prompt)
        self._active_sid = sid
        return sid

    def _save(self):
        with self._lock:
            c = self._ctx()
            msgs = c["sess"]["messages"]
            if not any(m.get("role") == "user" for m in msgs):
                return
            store.save(c["sid"], {"title": c["sess"]["title"] or "未命名会话",
                                  "messages": msgs, "slog_path": c["slog"].path,
                                  "persona": c["persona"], "model": c["active"],
                                  "mode": c["mode"],
                                  "saved_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

    def new_session(self):
        """运行中也能新建: 旧任务的轮次绑在自己的线程 ctx 上继续后台跑,
        新任务用默认专家/模型, 两者 messages/提示词/状态完全隔离。"""
        self._save()
        return {"ok": True, "sid": self._new_session()}

    def open_session(self, sid):
        """运行中也能切换; 切回内存里的旧任务直接复用它的完整运行态。"""
        sid = str(sid)
        if sid in self._ctxs:
            if self._active_sid != sid:
                self._save()
            self._ctxs[sid]["unread"] = False   # 用户看过了
            self._active_sid = sid
            return {"ok": True, "sid": sid}
        d = store.load(sid)
        if not d:
            return {"error": "会话不存在"}
        self._save()
        msgs = d.get("messages") or []
        if not msgs or msgs[0].get("role") != "system":
            msgs.insert(0, {"role": "system", "content": ""})
        persona = d.get("persona") if d.get("persona") in PERSONAS else self._boot["persona"]
        mode = "execute" if d.get("mode") == "execute" else "query"
        model = d.get("model") if d.get("model") in self.profiles else self._boot["active"]
        prompt = build_system_prompt(self.cfg, persona, mode)
        msgs[0]["content"] = prompt           # 用该任务自己的专家/模式, 不是当前激活会话的
        repair_messages(msgs)
        sp = d.get("slog_path")
        slog = Slog(path=sp) if sp and os.path.exists(sp) else Slog()
        sess = {"messages": msgs, "title": d.get("title") or "",
                "tool_log": [], "slog_path": slog.path}
        ctx = self._make_ctx(sid, sess, slog, persona=persona, mode=mode,
                             active=model, system_prompt=prompt)
        self._active_sid = sid
        slog.log("系统", "恢复会话: %s" % (sess["title"] or sid))
        with self._bound(ctx):
            self._maybe_compact()
        return {"ok": True, "sid": sid}

    def delete_session(self, sid):
        sid = str(sid)
        if sid == self._active_sid:
            return {"error": "不能删除当前任务"}
        c = self._ctxs.get(sid)
        if c and c["busy"]:
            return {"error": "该任务正在运行, 停止后再删除"}
        self._ctxs.pop(sid, None)
        return {"ok": store.delete(sid)}

    def list_sessions(self):
        """磁盘列表 + 每个任务的实时状态(专家/模型/运行中/是否激活)。
        刚新建、还没落盘的空任务也要列出来, 否则用户点了新建却看不见。"""
        out = store.list_sessions()
        by_id = {x["id"]: x for x in out}
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for sid, c in self._ctxs.items():
            it = by_id.get(sid)
            if it is None:
                it = {"id": sid, "title": c["sess"]["title"] or "新任务",
                      "n_msgs": 0, "saved_at": now}
                out.append(it)
            spec = PERSONAS.get(c["persona"]) or {}
            it.update({"persona": c["persona"],
                       "persona_label": spec.get("label", c["persona"]),
                       "persona_icon": spec.get("icon", "🧠"),
                       "model": (self.profiles.get(c["active"]) or {}).get("model") or c["active"],
                       "mode": c["mode"], "busy": c["busy"],
                       "active": sid == self._active_sid,
                       "unread": bool(c.get("unread")),
                       "stage": self._stage(c),
                       "n_msgs": max(it.get("n_msgs") or 0,
                                     len([m for m in c["sess"]["messages"]
                                          if m.get("role") in ("user", "assistant")]))})
        out.sort(key=lambda x: x.get("saved_at") or "", reverse=True)
        return out

    def history(self):
        """前端重放的消息(user/assistant)。"""
        out = []
        for m in self.sess["messages"]:
            r = m.get("role")
            if r == "user":
                out.append({"kind": "user", "text": str(m.get("content") or "")[:20000]})
            elif r == "assistant" and m.get("content"):
                out.append({"kind": "bot",
                            "text": strip_gate_banner(str(m.get("content")))[:20000]})
        return out

    # ---------- 设置 ----------

    def set_mode(self, mode):
        if self.busy:
            return {"error": "运行中不能切模式"}
        self.mode = "execute" if mode == "execute" else "query"
        # 能力清单必须跟着模式重建: 否则只读时提示词仍宣称 shell/run_python 可用,
        # 或切到可执行后模型仍以为没有写工具, 两边都会空转。
        self.system_prompt = build_system_prompt(self.cfg, self.persona, self.mode)
        if self.sess["messages"] and self.sess["messages"][0].get("role") == "system":
            self.sess["messages"][0]["content"] = self.system_prompt
        self.sess["messages"].append(
            {"role": "system", "content": "[系统] 用户已切换到%s模式" %
             ("可执行" if self.mode == "execute" else "只读查询")})
        self.slog.log("系统", "切换模式: %s" % self.mode)
        return {"ok": True}

    def set_model(self, key):
        """模型是会话级: 只改当前任务, 新开的任务可以选另一个模型, 互不影响。"""
        if key not in self.profiles:
            return {"error": "未知模型profile"}
        self.active = key
        self.slog.log("系统", "切换模型: %s" % key)
        self._save()                 # 连同 persona/mode 一起落到本任务的会话文件
        return {"ok": True}

    def _persist_ui(self, **kv):
        """把界面偏好(lang / persona)写进 devices.json 的 ui 段。
        与语言共用一节: 客户端重启后能"回到上次退出时的样子", 又不额外造状态文件;
        写盘失败只记日志——偏好丢了不该影响当前这一轮能不能用。"""
        ui = dict(self.cfg.get("ui") or {})
        ui.update({k: v for k, v in kv.items() if v is not None})
        self.cfg["ui"] = ui
        try:
            path = cfg_path()
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            raw["ui"] = ui
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            self.slog.log("系统", "界面偏好落盘失败: %s" % str(e)[:120])
        return ui

    def set_lang(self, lang):
        """界面/回复语言切换(zh|en)。立即重建系统提示词(模型下一轮按新语言回答),
        并落盘到 devices.json 的 ui.lang —— 前端启动时用 state().lang 还原界面。"""
        v = "en" if str(lang or "").strip().lower().startswith("en") else "zh"
        if v == self.lang and _ui_lang(self.cfg) == v:
            return {"ok": True, "lang": v}
        self.lang = v
        self._persist_ui(lang=v)
        # 界面/回复语言是全局偏好, 但每个任务都有自己的系统提示词, 要逐个重建
        for c in list(self._ctxs.values()):
            with self._bound(c):
                self.system_prompt = build_system_prompt(self.cfg, self.persona, self.mode)
                if self.sess["messages"]:
                    self.sess["messages"][0]["content"] = self.system_prompt
        self.slog.log("系统", "切换语言: %s" % v)
        return {"ok": True, "lang": v}

    def set_notify(self, mode):
        """任务完成桌面提示策略(auto|always|off)，落盘到 ui.notify。
        只影响提示，不影响任务本身；写盘失败也只记日志。"""
        v = str(mode or "").strip().lower()
        if v not in ("auto", "always", "off"):
            return {"error": "无效的提示模式"}
        self._persist_ui(notify=v)
        return {"ok": True, "notify": v}

    def set_persona(self, persona):
        """切换专家(安全运营/通用编码/自建专家)。切换即更新系统提示词, 下一轮生效;
        写操作权限回退只读——不同专家的可执行面不同, 避免沿用上一专家的授权惯性。
        当前专家记进配置(ui.persona): 下次启动直接回到这位专家。"""
        if self.busy:
            return {"error": "运行中不能切换专家, 请先停止本轮"}
        persona = str(persona or "").strip().lower()
        if persona not in PERSONAS:
            return {"error": "未知专家: %r (可用: %s)" % (persona, ", ".join(PERSONAS))}
        if persona == self.persona:
            self._persist_ui(persona=persona)     # 配置里可能还没记过, 顺手补上
            return {"ok": True, "note": "already"}
        old = PERSONAS[self.persona]["label"]
        self.persona = persona
        self._persist_ui(persona=persona)
        self.system_prompt = build_system_prompt(self.cfg, persona, "query")
        self.mode = "query"
        self.sess["messages"][0]["content"] = self.system_prompt
        self.sess["messages"].append(
            {"role": "system",
             "content": "[系统] 用户已把专家从「%s」切换到「%s」。本条之后请完全按新专家的"
                        "系统提示词工作; 此前的角色化指令不再约束你。写操作需用户重新切换"
                        "到可执行模式。" % (old, PERSONAS[persona]["label"])})
        self.slog.log("系统", "切换专家: %s -> %s" % (old, persona))
        self._save()
        return {"ok": True, "persona": persona, "label": PERSONAS[persona]["label"]}

    # ---------- 专家中心: 自建专家的增删查 ----------

    def personas_list(self):
        """专家清单(内置在前, 自建在后), 供专家中心渲染卡片。"""
        return {"personas": [
            {"id": k, "label": v.get("label"), "icon": v.get("icon", "🧠"),
             "desc": v.get("desc", ""), "welcome": v.get("welcome", ""),
             "secops": bool(v.get("secops")), "builtin": bool(v.get("builtin")),
             "custom": bool(v.get("custom")), "scope": v.get("scope"),
             "file": v.get("file"),
             "cards": (list(v.get("cards")) if v.get("cards")
                       else _generic_cards(v.get("label")))}
            for k, v in PERSONAS.items()]}

    def persona_cards(self):
        """当前专家的欢迎页能力卡片(内置专家各配4张, 自建专家用通用卡)。"""
        v = PERSONAS.get(self.persona) or {}
        return list(v.get("cards") or _generic_cards(v.get("label")))

    def persona_create(self, label, desc, prompt, icon="", tools="general", welcome=""):
        """新建自建专家: 写入 <ROOT>/agents/personas/<slug>.md, 热注册(不重启生效)。"""
        label = " ".join(str(label or "").split())
        desc = " ".join(str(desc or "").split())
        welcome = " ".join(str(welcome or "").split())
        icon = " ".join(str(icon or "").split()) or "🧠"
        prompt = str(prompt or "").strip()
        if not label or not prompt:
            return {"error": "专家名称与提示词不能为空"}
        if len(prompt) > 20000:
            return {"error": "提示词过长(上限 20000 字符)"}
        slug = re.sub(r"[^0-9A-Za-z_-]+", "-", label).strip("-").lower()[:40]
        if not slug:
            slug = "expert-%s" % time.strftime("%m%d%H%M%S")
        base, slug = slug, slug
        while slug in PERSONAS:
            slug = "%s-%s" % (base, time.strftime("%H%M%S"))
        d = os.path.join(ROOT, "agents", "personas")
        try:
            os.makedirs(d, exist_ok=True)
            fn = slug + ".md"
            text = ("---\nlabel: %s\nicon: %s\ndescription: %s\ntools: %s\nwelcome: %s\n---\n\n%s\n"
                    % (label, icon, desc, "secops" if tools == "secops" else "general",
                       welcome or "把任务交给「%s」，我将按专家提示词工作。" % label, prompt))
            with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            return {"error": "写入专家文件失败: %s" % e}
        _reload_personas()
        return {"ok": True, "id": slug, "label": label,
                "note": "已注册到 agents/personas/%s.md, 立即生效" % fn}

    def persona_delete(self, pid):
        """删除自建专家(内置不可删); 删除前备份到 logs/deleted-personas/。"""
        spec = PERSONAS.get(str(pid or ""))
        if not spec:
            return {"error": "专家不存在"}
        if not spec.get("custom"):
            return {"error": "内置专家不可删除"}
        d = dict(_persona_dirs())[spec.get("scope") or "project"]
        path = os.path.join(d, spec.get("file") or "")
        if not os.path.isfile(path):
            return {"error": "专家文件不存在: %s" % path}
        try:
            bak = os.path.join(ROOT, "logs", "deleted-personas")
            os.makedirs(bak, exist_ok=True)
            shutil.copy2(path, os.path.join(bak, spec["file"]))
            os.remove(path)
        except Exception as e:
            return {"error": "删除失败: %s" % e}
        _reload_personas()
        note = "已删除并备份到 logs/deleted-personas/%s" % spec["file"]
        if self.persona == pid:               # 当前专家被删: 切回安全运营
            self.persona = "ezcode"
            self._persist_ui(persona="ezcode")   # 别让下次启动去恢复一个已删除的专家
            self.system_prompt = build_system_prompt(self.cfg, "ezcode", "query")
            self.sess["messages"][0]["content"] = self.system_prompt
            note += "; 当前专家已切回「安全运营」"
        # 后台/其它任务也可能绑着这位专家: 一并回落, 否则切回去会用到失效的提示词
        for c in self._ctxs.values():
            if c["persona"] == pid:
                c["persona"] = "ezcode"
                c["system_prompt"] = build_system_prompt(self.cfg, "ezcode", "query")
                if c["sess"].get("messages"):
                    c["sess"]["messages"][0]["content"] = c["system_prompt"]
        return {"ok": True, "note": note}

    # ---------- 客户端设置中心: 全节配置读写 ----------

    # 可编辑节(与devices.json顶层键一一对应; llm为{active,profiles})
    SETTING_SECTIONS = ("llm", "netbox", "edr", "ngsoc",
                        "rag", "custom_api", "mcp", "builtin_tools", "projects", "connectors")

    def settings(self):
        """读取全部可编辑节(原文, 不经_normalize), 供设置中心编辑。"""
        path = cfg_path()
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            return {"error": "读取配置失败: %s" % e}
        out = {"path": path}
        for k in self.SETTING_SECTIONS:
            out[k] = raw.get(k)
        # 告警检索站点卡片化: 内联sites为空而外部文件有站点时, 自动导入为可编辑卡片;
        # 保存即内联化(前端会清空sites_file)——url+key直配, 不再依赖外部文件。
        n = out.get("ngsoc")
        if isinstance(n, dict) and not n.get("sites") and n.get("sites_file"):
            try:
                with open(n["sites_file"], encoding="utf-8") as f:
                    imported = json.load(f).get("sites", [])
                if imported:
                    n = dict(n, sites=imported, _imported=n["sites_file"])
                    out["ngsoc"] = n
            except Exception:
                pass
        # llm缺省兜底(与llm_config一致)
        if not isinstance(out.get("llm"), dict) or not (out["llm"] or {}).get("profiles"):
            out["llm"] = {"active": self.active,
                          "profiles": {k: dict(v) for k, v in self.profiles.items()}}
        # 项目=设备组: devices.json 还没有 projects 时, 返回内存迁移结果(来自旧站点/项目/
        # 自定义接入), 否则设置页看不到任何设备。标记 _projects_migrated, 提示"保存后固化"。
        if not out.get("projects"):
            migrated = _ad_mod.build_projects(self.cfg)
            if migrated:
                out["projects"] = migrated
                out["_projects_migrated"] = True
        return out

    def save_settings(self, sections):
        """按节保存配置并热生效。
        sections: {节名: 整节数据}, 只接受SETTING_SECTIONS内的节, 整节替换。
        热生效策略:
          - 设备节(netbox/edr/ngsoc) → 全量configure(凭据变更),
            本会话'总是允许'授权会被重置(安全面: 换凭据后重新确认), 前端toast提示;
          - rag/custom_api → 只重注册这两个模块的工具(不动权限授权);
          - llm → 只更新profiles/active;
          - mcp → 热重连MCP服务器;
          - 任意节保存后重建系统提示词(能力清单可能变化)。"""
        if self.busy:
            return {"error": "运行中不能改配置, 请先停止本轮"}
        if not isinstance(sections, dict):
            return {"error": "配置数据格式错误"}
        changed = [k for k in sections if k in self.SETTING_SECTIONS]
        if not changed:
            return {"error": "没有可保存的配置节"}
        path = cfg_path()
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            return {"error": "读取配置失败: %s" % e}
        for k in changed:
            v = sections[k]
            if isinstance(v, dict):
                v = {ik: iv for ik, iv in v.items()
                     if not str(ik).startswith("_")}   # 剔除展示用标记(_imported等)
                sections[k] = v
            raw[k] = v
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)             # 原子替换
        except Exception as e:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return {"error": "写入配置失败: %s" % e}

        # ---- 热生效 ----
        notes = []
        need_full = [k for k in ("netbox", "edr", "ngsoc", "projects", "connectors") if k in changed]
        if need_full:
            self.cfg = load_config()           # 重读+normalize(新凭据/新项目生效)
            configure(self.cfg)
            notes.append("设备凭据已生效, 本会话的'总是允许'授权已重置")
        else:
            for k in changed:
                self.cfg[k] = sections[k]
        if "llm" in changed:
            self.profiles, self.active = llm_profiles(self.cfg)
        if "rag" in changed:
            _rag_mod.configure(self.cfg)
        if "custom_api" in changed:
            _api_mod.configure(self.cfg)
        if "builtin_tools" in changed:
            _ana_mod.configure(self.cfg)
            _apply_builtin(self.cfg)   # 内置工具箱分组开关(fs/file_ops/shell/web/todo)热生效
        if "mcp" in changed:
            try:
                r = plugin_loader.reload_mcp(self.cfg)
                for w in (r.get("warnings") or [])[:3]:
                    notes.append(w)
                notes.append("MCP已重连: %s台服务器/%s个工具"
                             % (len(r["loaded"].get("mcp_servers") or []),
                                r["loaded"].get("mcp_tools", 0)))
            except Exception as e:
                notes.append("MCP重载失败: %s" % str(e)[:120])
        # 能力清单可能变化(主机侧项目/RAG/自定义API), 重建系统提示词
        self.system_prompt = build_system_prompt(self.cfg, self.persona, self.mode)
        self.sess["messages"][0]["content"] = self.system_prompt
        self.slog.log("系统", "更新配置节: %s" % ", ".join(changed))
        return {"ok": True, "changed": changed,
                "note": "; ".join(notes) if notes else "已保存并生效"}

    # ---------- 自动适配: 设备取样 → 模型生成字段映射 → 校验 → 固化 ----------

    def _find_device(self, project, device):
        """在项目设备组里找设备(project 为空则全局按名称找)。"""
        for p in (self.cfg.get("projects") or []):
            if project and p.get("name") != project:
                continue
            for d in (p.get("devices") or []):
                if d.get("name") == device:
                    return p, d
        return None, None

    def sec_probe_list(self):
        """设置页: 逐设备自检(token 权限/可达性/能拉到多少条)。"""
        try:
            return _secops_mod.sec_probe()
        except Exception as e:
            return {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}

    def connector_status(self):
        """设置页「连接器」: 各能力域状态(Office 检测 + 计数)。"""
        out = {}
        try:
            out["office"] = _office_mod.detect_summary().replace("文档处理: ", "")
        except Exception:
            out["office"] = "检测失败"
        cfg = self.cfg or {}
        out["custom_api"] = len(((cfg.get("custom_api") or {}).get("endpoints")) or [])
        out["mcp"] = len(((cfg.get("mcp") or {}).get("servers")) or [])
        _rag = cfg.get("rag") or {}
        out["rag"] = bool(_rag.get("enabled") and _rag.get("url"))
        try:
            out["plugins"] = len((self.plugins_list() or {}).get("plugins") or [])
        except Exception:
            out["plugins"] = 0
        try:
            out["skills"] = len((self.skills_list() or {}).get("skills") or [])
        except Exception:
            out["skills"] = 0
        try:
            out["todo"] = int(_todo_mod.todo_list().get("total_open") or 0)
        except Exception:
            out["todo"] = 0
        return out

    def rag_test(self, cfg=None):
        """设置页: 用当前表单里的 RAG 配置真发一次检索(不落盘), 返回耗时/字符数/首段。"""
        try:
            return _rag_mod.rag_test(cfg or {})
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

    def sec_sample(self, project=None, device=None):
        """设置页: 取一台设备的响应样本(脱敏) + 结构摘要, 供用户核对后再生成映射。"""
        p, d = self._find_device(project, device)
        if not d:
            return {"ok": False, "error": "找不到设备 %r" % (device,)}
        r = _ad_mod.sample_request(d)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error") or "取样失败"}
        data = r["data"]
        return {"ok": True, "device": d.get("name"), "project": (p or {}).get("name"),
                "sample": _ad_mod.redact(data), "paths": _ad_mod.flatten_paths(data)}

    SEC_ADAPT_SYSTEM = (
        "你是安全设备的 API 字段映射助手。用户会给一段设备告警接口的真实响应样本(字符串值已脱敏)，"
        "你要输出一份**声明式字段映射 JSON**，把该响应映射成统一告警对象。\n\n"
        "统一告警对象字段: alarm_id, name, severity, severity_label, time, src_ip, dst_ip, "
        "dst_port, host, user, count, status\n\n"
        "输出格式(只输出这个 JSON，不要解释、不要 markdown 代码块):\n"
        "{\n"
        '  "request": {"method": "GET 或 POST", "path": "接口路径", "body_template": 对象或省略},\n'
        '  "response": {\n'
        '    "list_path": "告警数组在响应里的点路径",\n'
        '    "total_path": "总数路径(可选)",\n'
        '    "fields": {\n'
        '      "alarm_id": "字段路径", "name": "字段路径",\n'
        '      "severity": {"path": "字段路径", "map": {"原值": "信息/低/中/高/危急"}},\n'
        '      "time": {"path": "字段路径", "format": "ms/s/iso8601/strptime:%Y-%m-%d %H:%M:%S"},\n'
        '      "src_ip": "字段路径", "dst_ip": "字段路径", "dst_port": "字段路径",\n'
        '      "host": "字段路径", "user": "字段路径", "count": "字段路径", "status": "字段路径"\n'
        "    },\n"
        '    "fingerprint_keys": ["src_ip", "dst_ip", "dst_port", "name"]\n'
        "  },\n"
        '  "detail": {"path": "详情接口路径(用 {alarm_id} 占位)", "raw_path": "报文所在路径"}\n'
        "}\n\n"
        "规则:\n"
        "1. 路径用点号分隔(如 data.list)；字符串数组不带索引。\n"
        "2. 认不出的字段直接省略, 不要编造不存在的路径。\n"
        "3. 时间格式必须判断准确: 13位数字=ms, 10位数字=s, 带 T 或时区=iso8601。\n"
        "4. severity 如果是文字(high/中危/critical), 也要给 map 做归一化。\n"
        "5. list_path 必须是样本里真实存在的数组路径。\n"
        "6. 如果用户给了接口文档, 优先按文档确认字段语义、请求参数与鉴权方式; "
        "文档与真实样本冲突时, 以样本的实际结构为准。"
    )

    @staticmethod
    def _parse_mapping(text):
        """从模型输出里抠出映射 JSON, 返回 (mapping, error)。"""
        s = (text or "").strip()
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S).strip()
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            return None, "输出里没有 JSON 对象"
        try:
            m = json.loads(s[i:j + 1])
        except Exception as e:
            return None, "JSON 解析失败: %s" % str(e)[:120]
        if not isinstance(m.get("response"), dict):
            return None, "缺少 response 节"
        if not (m["response"].get("list_path") or "").strip():
            return None, "缺少 response.list_path"
        if not isinstance(m["response"].get("fields"), dict):
            return None, "缺少 response.fields"
        return m, None

    def sec_adapt(self, project=None, device=None):
        """自动适配: 取样 → 模型生成映射 → 用真实样本校验(失败回炉≤2次) → 返回草案(不落盘)。"""
        p, d = self._find_device(project, device)
        if not d:
            return {"ok": False, "error": "找不到设备 %r" % (device,)}
        if str(d.get("kind") or "http") != "http":
            return {"ok": False, "error": "%s 类设备已内置适配器, 无需生成映射" % d.get("kind")}
        r = _ad_mod.sample_request(d)
        if not r.get("ok"):
            return {"ok": False, "error": "取样失败: %s" % (r.get("error") or "接口无响应"),
                    "hint": "先在设置里填好地址与鉴权, 确认接口路径正确"}
        data = r["data"]
        paths = _ad_mod.flatten_paths(data)
        sample_text = json.dumps(_ad_mod.redact(data), ensure_ascii=False)[:8000]
        doc = str(d.get("api_doc") or "").strip()
        user = "设备: %s\n接口: %s\n" % (d.get("name"), d.get("base_url"))
        if doc:
            user += "\n接口文档(节选, 用于确认字段语义与请求参数):\n%s\n" % doc[:6000]
        user += ("\n结构摘要:\n%s\n\n响应样本(字符串已脱敏):\n%s"
                 % ("\n".join(paths[:80]), sample_text))
        last_err = None
        for _ in range(3):
            try:
                text = L.oneshot(self.profile, self.active, self.SEC_ADAPT_SYSTEM,
                                 user, max_chars=8000)
            except Exception as e:
                return {"ok": False, "error": "调用模型失败: %s" % str(e)[:200],
                        "hint": "先在「大模型」里配置可用的 API Key"}
            mapping, err = self._parse_mapping(text)
            if mapping:
                ok, rows, verr = _ad_mod.validate_mapping(mapping, data)
                if ok:
                    return {"ok": True, "device": d.get("name"), "project": (p or {}).get("name"),
                            "mapping": mapping, "preview": rows, "fields": paths,
                            "note": "已用真实样本校验通过, 确认无误后点「保存映射」生效"}
                last_err = verr
            else:
                last_err = err
            user += "\n\n上一次输出不可用: %s\n请修正后重新只输出 JSON。" % last_err
        return {"ok": False, "error": "模型连续 3 次未生成可用映射: %s" % last_err,
                "sample": _ad_mod.redact(data), "paths": paths}

    def sec_apply_mapping(self, project, device, mapping):
        """保存映射到 devices.json 的项目设备组, 并热生效。"""
        if not isinstance(mapping, dict) or not (mapping.get("response") or {}).get("list_path"):
            return {"ok": False, "error": "映射结构不合法(缺少 response.list_path)"}
        p, d = self._find_device(project, device)
        if not d:
            return {"ok": False, "error": "找不到设备 %r" % (device,)}
        d["mapping"] = mapping
        try:
            path = cfg_path()
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            raw["projects"] = self.cfg.get("projects") or []
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return {"ok": False, "error": "写入配置失败: %s" % str(e)[:160]}
        self.cfg = load_config()
        configure(self.cfg)
        self.system_prompt = build_system_prompt(self.cfg, self.persona, self.mode)
        self.sess["messages"][0]["content"] = self.system_prompt
        return {"ok": True, "note": "映射已保存并生效(原有站点/项目配置保留不动)"}

    def mcp_test(self, spec):
        """测试一台MCP服务器的连通性(不落盘): 连接→列工具→返回。"""
        if not isinstance(spec, dict) or not spec.get("name"):
            return {"ok": False, "error": "缺少服务器名称"}
        import mcp_client
        srv = None
        try:
            srv = mcp_client.connect_server(spec)
            tools = srv.list_tools() or []
            return {"ok": True, "n_tools": len(tools),
                    "tools": [t.get("name") for t in tools][:20]}
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
        finally:
            try:
                if srv is not None:
                    srv.close()
            except Exception:
                pass

    # ---------- Skills(全局+项目级, 设置中心可管理) ----------

    def _refresh_system_prompt(self):
        self.system_prompt = build_system_prompt(self.cfg, self.persona, self.mode)
        self.sess["messages"][0]["content"] = self.system_prompt

    def skills_list(self):
        try:
            return {"skills": skills_lib.list_skills(),
                    "global_dir": skills_lib._GLOBAL_DIR,
                    "project_dir": skills_lib._project_dir()}
        except Exception as e:
            return {"error": "读取技能失败: %s" % e}

    def skills_save(self, scope, filename, name, description, enabled, content):
        r = skills_lib.save(scope, filename, name, description, enabled, content)
        if r.get("ok"):
            self._refresh_system_prompt()      # 技能变更下一轮生效
        return r

    def skills_delete(self, scope, filename):
        r = skills_lib.delete(scope, filename)
        if r.get("ok"):
            self._refresh_system_prompt()
        return r

    # ---------- 插件管理(设置中心「插件」分区; 安装/启停/卸载全部热生效) ----------

    @staticmethod
    def _plugin_name_ok(filename):
        import re as _re
        ok = bool(_re.fullmatch(r"[A-Za-z0-9_\-\u4e00-\u9fff]+\.py", str(filename or "")))
        return ok and ".." not in str(filename)

    def plugins_list(self):
        """扫描plugins/目录: 文件、启用状态、各插件注册的工具、加载警告。"""
        disabled = set((self.cfg.get("plugins") or {}).get("disabled") or [])
        items = []
        for fn, name in plugin_loader.plugin_files():
            tools = plugin_loader._plugin_tools.get(name) or []
            warns = [w for w in plugin_loader.warnings()
                     if ("[%s]" % name) in w or ("插件%s" % name) in w]
            items.append({"file": fn, "name": name, "enabled": name not in disabled,
                          "tools": tools, "n_tools": len(tools),
                          "loaded": name in plugin_loader.status()["plugins"],
                          "warning": warns[:1]})
        return {"plugins": items, "dir": plugin_loader.PLUGIN_DIR}

    def _save_plugin_disabled(self, disabled):
        """plugins.disabled 写回 devices.json(原子) + 更新self.cfg。"""
        path = cfg_path()
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            return {"error": "读取配置失败: %s" % e}
        raw.setdefault("plugins", {})["disabled"] = sorted(disabled)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return {"error": "写入配置失败: %s" % e}
        self.cfg.setdefault("plugins", {})["disabled"] = sorted(disabled)
        return {"ok": True}

    def plugin_install(self, path=None, filename=None, code=None):
        """安装插件: path=复制外部.py; filename+code=粘贴代码安装。装完热加载。"""
        import re as _re
        import shutil as _shutil
        os.makedirs(plugin_loader.PLUGIN_DIR, exist_ok=True)
        if path:
            if not (os.path.isfile(str(path)) and str(path).endswith(".py")):
                return {"error": "只支持安装.py插件文件"}
            filename = os.path.basename(str(path))
            if not self._plugin_name_ok(filename):
                return {"error": "文件名含非法字符"}
            try:
                _shutil.copy(str(path), os.path.join(plugin_loader.PLUGIN_DIR, filename))
            except Exception as e:
                return {"error": "复制插件失败: %s" % e}
        elif code is not None:
            fn = str(filename or "")
            if not fn.endswith(".py"):
                fn += ".py"
            if not self._plugin_name_ok(fn) or not str(code).strip():
                return {"error": "文件名非法或代码为空"}
            filename = fn
            target = os.path.join(plugin_loader.PLUGIN_DIR, fn)
            try:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(str(code))
            except Exception as e:
                return {"error": "写入插件失败: %s" % e}
        else:
            return {"error": "缺少path或code"}
        name = os.path.splitext(str(filename))[0]
        # 之前被禁用的同名插件重新安装时自动启用
        disabled = set((self.cfg.get("plugins") or {}).get("disabled") or [])
        if name in disabled:
            disabled.discard(name)
            r = self._save_plugin_disabled(disabled)
            if r.get("error"):
                return r
        plugin_loader.reload_plugins(self.cfg)
        self._refresh_system_prompt()          # PLUGIN_PROMPT注入生效
        loaded = name in plugin_loader.status()["plugins"]
        return {"ok": loaded, "name": name,
                "note": "已加载" if loaded else "文件已安装但加载失败(见警告)",
                "warnings": [w for w in plugin_loader.warnings() if name in w][:2]}

    def plugin_set_enabled(self, filename, enabled):
        name = os.path.splitext(str(filename or ""))[0]
        disabled = set((self.cfg.get("plugins") or {}).get("disabled") or [])
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        r = self._save_plugin_disabled(disabled)
        if r.get("error"):
            return r
        plugin_loader.reload_plugins(self.cfg)
        self._refresh_system_prompt()
        return {"ok": True, "enabled": bool(enabled)}

    def plugin_delete(self, filename):
        fn = str(filename or "")
        if not self._plugin_name_ok(fn):
            return {"error": "非法文件名"}
        target = os.path.join(plugin_loader.PLUGIN_DIR, fn)
        try:
            os.remove(target)
        except OSError as e:
            return {"error": "删除失败: %s" % e}
        name = os.path.splitext(fn)[0]
        disabled = set((self.cfg.get("plugins") or {}).get("disabled") or [])
        disabled.discard(name)
        self._save_plugin_disabled(disabled)
        plugin_loader.reload_plugins(self.cfg)
        self._refresh_system_prompt()
        return {"ok": True}

    def llm_config(self):
        """返回当前配置文件中的完整 LLM 配置(供设置界面编辑)。"""
        path = cfg_path()
        raw = {}
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            return {"error": "读取配置失败: %s" % e}
        llm = raw.get("llm") or {}
        profiles = llm.get("profiles")
        if not isinstance(profiles, dict) or not profiles:
            profiles = {k: dict(v) for k, v in self.profiles.items()}
            llm = {"active": self.active, "profiles": profiles}
        return {"path": path, "active": llm.get("active") or self.active,
                "profiles": {k: dict(v) for k, v in profiles.items()}}

    @staticmethod
    def clean_llm_profile(v):
        """把前端/配置里的一条 profile 归一成落盘格式。

        保留 provider(厂商预设标记, 界面据此显示"已接入")与高级字段
        (temperature/verify_ssl/output), 其余未知键一律丢弃。"""
        item = {"base_url": str(v.get("base_url") or "").strip(),
                "api_key": str(v.get("api_key") or "").strip(),
                "model": str(v.get("model") or "").strip()}
        prov = str(v.get("provider") or "").strip()
        if prov:
            item["provider"] = prov
        if v.get("context"):
            try:
                item["context"] = int(v.get("context"))
            except (TypeError, ValueError):
                pass
        temp = v.get("temperature")
        if temp is not None and str(temp).strip() != "":
            try:
                item["temperature"] = float(temp)
            except (TypeError, ValueError):
                pass
        out = v.get("output")
        if out:
            try:
                item["output"] = int(out)
            except (TypeError, ValueError):
                pass
        if v.get("verify_ssl") is False:
            item["verify_ssl"] = False
        return item

    def test_llm_profile(self, profile):
        """测试一份模型配置能否连通(不写配置、不占会话上下文)。

        先试 GET /models(最省 token, 多数厂商支持), 不通再发一条极短对话。"""
        if not isinstance(profile, dict):
            return {"ok": False, "error": "配置格式错误"}
        p = self.clean_llm_profile(profile)
        if not p.get("base_url") or not p.get("model"):
            return {"ok": False, "error": "请先填写接口地址与模型名"}
        base = p["base_url"].rstrip("/")
        headers = {"Content-Type": "application/json"}
        if p.get("api_key"):
            headers["Authorization"] = "Bearer " + p["api_key"]
        verify = p.get("verify_ssl", True)

        def _hint(code, text):
            if code in (401, 403):
                return "Key 无效或没有该模型的权限(HTTP %s)" % code
            if code == 404:
                return "接口地址或模型名不对(HTTP 404), 检查 base_url 与模型名"
            if code == 429:
                return "触发限频或余额不足(HTTP 429)"
            return "HTTP %s: %s" % (code, (text or "")[:160])

        t0 = time.time()
        try:
            r = requests.get(base + "/models", headers=headers, timeout=(6, 12), verify=verify)
            if r.status_code == 200:
                return {"ok": True, "detail": "连接正常（%.1fs）" % (time.time() - t0)}
        except requests.RequestException:
            pass                       # /models 不可用时继续走真实对话
        body = {"model": p["model"],
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 16, "stream": False}
        try:
            r = requests.post(base + "/chat/completions", headers=headers, json=body,
                              timeout=(8, 25), verify=verify)
        except requests.RequestException as e:
            return {"ok": False, "error": "连接失败: %s" % str(e)[:160]}
        if r.status_code == 200:
            return {"ok": True, "detail": "连接正常，模型已响应（%.1fs）" % (time.time() - t0)}
        return {"ok": False, "error": _hint(r.status_code, r.text)}

    def save_llm_config(self, active, profiles):
        """保存 LLM 配置: 写回配置文件(保留其它字段)并热生效。
        profiles: {name: {base_url, api_key, model, [context], [provider]}}"""
        if self.busy:
            return {"error": "运行中不能改模型配置, 请先停止本轮"}
        if not isinstance(profiles, dict) or not profiles:
            return {"error": "至少保留一个模型配置"}
        clean = {}
        for name, v in profiles.items():
            name = str(name).strip()
            if not name or not isinstance(v, dict):
                continue
            item = self.clean_llm_profile(v)
            if not item["base_url"] or not item["model"]:
                return {"error": "配置「%s」缺少 base_url 或 model" % (name or "(未命名)")}
            clean[name] = item
        if not clean:
            return {"error": "没有可保存的有效配置"}
        active = str(active or "").strip()
        if active not in clean:
            active = next(iter(clean))
        # key 处理顺序: 先清空再赋值, 兼容改名/删除
        path = cfg_path()
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            return {"error": "读取配置失败: %s" % e}
        raw["llm"] = {"active": active, "profiles": clean}
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)             # 原子替换, 断电/崩溃不留半个配置
        except Exception as e:
            try:
                os.path.exists(tmp) and os.remove(tmp)
            except OSError:
                pass
            return {"error": "写入配置失败: %s" % e}
        # 热生效: 只更新LLM相关字段, 不重跑configure(避免清掉本会话已授权的权限)
        self.cfg["llm"] = {"active": active, "profiles": clean}
        self.profiles, self.active = llm_profiles(self.cfg)
        self.slog.log("系统", "更新模型配置: %s (active=%s)" % (", ".join(clean), active))
        return {"ok": True, "active": active}

    # ---------- 发送/中止 ----------

    def send(self, text):
        if self.busy:
            return {"error": "busy"}
        if getattr(self, "_compacting", False):
            return {"error": "正在压缩上下文, 请稍候"}
        text = str(text or "").strip()
        if not text:
            return {"error": "empty"}
        if "@" in text:
            text = expand_at_files(text, list(SUBAGENTS.keys()))
        self.sess["messages"][0]["content"] = self.system_prompt
        self.sess["messages"].append({"role": "user", "content": text})
        self.slog.log("用户", text)
        threading.Thread(target=self._run_round, args=(self.sid,), daemon=True).start()
        return {"ok": True}

    def stop(self, sid=None):
        """stop 只作用于指定(缺省=当前激活)任务; 后台任务不会被当前任务的中止误伤。"""
        c = self._ctx_for(sid)
        if c is not None:
            c["cancel"].set()
        return {"ok": True}

    # ---------- 权限确认(emit confirm → 前端回选) ----------

    def _confirm_gate(self, action, resource, detail):
        self._confirm_seq += 1
        seq = self._confirm_seq
        ev = threading.Event()
        box = {"choice": None}
        self._confirm_waiters[seq] = (ev, box)
        c = self._ctx()
        # 带上来源任务标题: 后台任务的确认会在当前界面直接弹(不排队等切回),
        # 用户需要知道"这条命令是哪个任务要执行的", 弹窗里会标注来源。
        self._emit_safe({"t": "confirm", "seq": seq, "action": action,
                         "resource": str(resource)[:300],
                         "detail": str(detail)[:4000],
                         "title": (c.get("sess") or {}).get("title") or ""})
        ev.wait(CONFIRM_TIMEOUT)
        self._confirm_waiters.pop(seq, None)
        c = box["choice"]
        if c == "always":
            try:
                perms.grant_session(action, "*", scope=self._ctx().get("sid"))
            except Exception:
                pass
        return c in ("once", "always")

    def confirm_result(self, seq, choice):
        """前端回选。严格归一化: 只接受 'once'/'always' 字符串, 其他一律拒绝。"""
        choice = "once" if choice is True else str(choice or "deny")
        if choice not in ("once", "always"):
            choice = "deny"
        w = self._confirm_waiters.get(int(seq))
        if w:
            w[1]["choice"] = choice
            w[0].set()
        return {"ok": True}

    # ---------- 主循环 ----------

    @staticmethod
    def _stage(c):
        """任务列表里给后台任务看的实时阶段(结构化, 由前端按语言渲染)。"""
        return c.get("stage") if c.get("busy") else None

    def _emit_safe(self, payload):
        try:
            c = self._ctx()
            t = payload.get("t")
            # 轻量记录本任务当前在干什么; 前端切到别的任务时, 列表里仍能看到
            # 后台任务的实时阶段, 而不是"运行中"四个字一动不动。
            if t == "think":
                c["stage"] = {"k": "think", "n": payload.get("chars") or 0}
            elif t == "text":
                c["stage"] = {"k": "text", "n": len(payload.get("text") or "")}
            elif t == "tool":
                c["stage"] = {"k": "tool", "n": payload.get("name") or ""}
            elif t == "sub":
                c["stage"] = {"k": "sub", "n": ""}
            elif t in ("round_end", "error"):
                c["stage"] = None
                # 后台任务跑完而用户没在看: 打未读标记, 任务列表显示"有新回答"
                if c["sid"] != self._active_sid:
                    c["unread"] = True
            # 每个事件都打上所属任务 sid: 前端只把激活任务的事件写进对话 DOM,
            # 后台任务的事件进缓存(切回时补渲染), 绝不串台。
            self.emit(dict(payload, sid=c["sid"]))
        except Exception:
            pass

    def _run_round(self, sid):
        ctx = self._ctxs.get(sid)
        if ctx is None:
            return
        # 关键: 把本轮绑定到它自己的会话 ctx。此后本线程里所有 self.sess /
        # self.busy / self.active / self.cancel 都落在这份状态上, 即使主线程
        # 中途切换到别的任务, 这一轮仍读写原来的任务。
        self._tls.ctx = ctx
        self.busy = True
        self.cancel = threading.Event()
        t0 = time.time()
        n_tools = n_think = 0
        try:
            answer, n_tools, n_think = self._agent_loop(self.sess["messages"], is_sub=False)
            # 必须先复位 busy 再发 round_end: 前端收尾会 refreshState(), 若此时
            # 引擎仍 busy=True 会把界面重新锁成"运行中", 用户无法再发消息
            self.busy = False
            _spec = PERSONAS.get(self.persona) or PERSONAS["ezcode"]
            self._emit_safe({"t": "round_end", "answer": answer,
                             "dt": round(time.time() - t0, 1), "n_tools": n_tools,
                             "think": n_think, "ctx_pct": self.ctx_pct(),
                             "title": self.sess.get("title") or "",
                             "persona_label": _spec.get("label", ""),
                             "persona_icon": _spec.get("icon", "🧠")})
            self._maybe_title()          # 标题生成要再调一次LLM, 不占用"运行中"状态
            self._maybe_compact()        # 水位≥80%时后台摘要压缩(轮间执行, 不阻塞)
        except Cancelled:
            repair_messages(self.sess["messages"])
            self.slog.log("系统", "用户中止本轮, 已清理残缺消息")
            self.busy = False
            self._emit_safe({"t": "round_end", "answer": "", "cancelled": True,
                             "dt": round(time.time() - t0, 1), "n_tools": n_tools,
                             "think": n_think, "ctx_pct": self.ctx_pct()})
        except Exception as e:
            repair_messages(self.sess["messages"])
            err = "引擎异常: %s: %s" % (type(e).__name__, str(e)[:500])
            self.slog.log("系统", err)
            self.busy = False
            self._emit_safe({"t": "error", "text": err, "ctx_pct": self.ctx_pct()})
        finally:
            self.busy = False
            self._save()

    def _maybe_title(self):
        if self.sess["title"]:
            return
        users = [m.get("content") for m in self.sess["messages"] if m.get("role") == "user"]
        if not users:
            return
        try:
            title = L.oneshot(self.profile, self.active,
                              "为下面的对话起一个不超过12字的中文标题, 只输出标题本身, 不要引号。",
                              str(users[-1])[:600], max_chars=60).strip()
            title = title.splitlines()[0][:20]
            if title:
                self.sess["title"] = title
                self.slog.log("会话标题", title)
                self._emit_safe({"t": "title", "title": title})
                self._save()             # 标题可能在主轮次落盘后才生成, 补存一次
        except Exception:
            pass

    # ---------- 上下文压缩(LLM摘要式, 对齐Claude Code compact) ----------
    # 两层机制: _agent_loop每轮的确定性截断(便宜兜底) + 本摘要压缩(语义保真):
    # 历史消息→LLM生成纪要, 保留最近对话原文; 会话文件(slog/store)始终全量留存审计。

    COMPACT_THRESHOLD = 80        # 水位阈值(%), 超过自动压缩
    COMPACT_KEEP_TAIL = 8         # 压缩时保留最近N条消息原文

    def compact_now(self):
        """手动压缩(js_api): force无视水位阈值。"""
        if self.busy:
            return {"error": "运行中不能压缩, 请等待本轮结束"}
        if not self._maybe_compact(force=True):
            return {"error": "消息太少, 无需压缩"}
        return {"ok": True}

    def _maybe_compact(self, force=False):
        """水位≥阈值(或force)时后台启动摘要压缩。返回是否已启动。"""
        if self.busy or getattr(self, "_compacting", False):
            return False
        msgs = self.sess["messages"]
        if len(msgs) < 10:         # system+<9条: 压无可压
            return False
        if not force and self.ctx_pct() < self.COMPACT_THRESHOLD:
            return False
        threading.Thread(target=self._do_compact, args=(self.sid,), daemon=True).start()
        return True

    def _do_compact(self, sid=None):
        # 压缩在独立线程跑, 必须重新绑定它所属的任务, 否则会压到当前激活的那个
        c = self._ctxs.get(sid) if sid else None
        if c is not None:
            self._tls.ctx = c
        with self._lock:
            if getattr(self, "_compacting", False):
                return
            self._compacting = True
        try:
            old_pct = self.ctx_pct()
            msgs = self.sess["messages"]
            keep = self.COMPACT_KEEP_TAIL
            head = msgs[1:-keep]                     # system除外, 尾部保留区除外
            if len(head) < 4:
                return
            history_text = "\n".join(
                "[%s] %s" % (m.get("role"), str(m.get("content") or "")[:1500])
                for m in head)[:60000]
            summary = L.oneshot(
                self.profile, self.active,
                "把下面的多轮安全运营对话历史压缩成精炼纪要, 作为后续对话的唯一历史上下文。"
                "必须保留: ①每轮告警研判的最终结论(误报族名/定性/关键证据锚点) "
                "②主机/IP资产归属结论 ③用户交代的背景与要求 ④待办或未确认事项 "
                "⑤工具返回的关键数据(样本哈希/时间/命令)。"
                "丢弃: 工具原始输出细节、过程性说明、已结束轮次的中间推理。"
                "直接输出纪要正文, 不要客套。",
                history_text, max_chars=4000)
            summary = (summary or "").strip()
            if len(summary) < 40:
                self.slog.log("系统", "上下文压缩失败: 摘要过短, 放弃")
                return
            new_msgs = [msgs[0],
                        {"role": "user", "content":
                         "[上一段会话历史的压缩纪要, 供参考; 最近对话附后]\n" + summary}]
            new_msgs += msgs[-keep:]
            self.sess["messages"] = new_msgs
            self.slog.log("系统", "上下文压缩: %d条历史→%d字纪要+保留最近%d条"
                          % (len(head), len(summary), keep))
            self._emit_safe({"t": "notice", "ctx_pct": self.ctx_pct(),
                             "text": "上下文已压缩: %d 条历史消息 → %d 字纪要 + 最近 %d 条原文"
                                     " (水位 %d%% → %d%%)"
                                     % (len(head), len(summary), keep, old_pct, self.ctx_pct())})
            self._save()
        except Exception as e:
            self.slog.log("系统", "上下文压缩失败: %s" % str(e)[:200])
        finally:
            self._compacting = False

    def _agent_loop(self, msgs, is_sub, max_rounds=None, push=None):
        """Agent 循环。is_sub=True为子代理(push=心跳回调, 不推主界面);
        主会话则全部事件推给 emit。返回 (answer, n_tools, n_think)。"""
        rounds = int(max_rounds) if max_rounds else MAX_ROUNDS
        secops = bool((PERSONAS.get(self.persona) or {}).get("secops"))
        tools_list = toolset("query", self.persona, secops=secops) if is_sub \
            else (toolset(self.mode, self.persona, secops=secops) + [subagent_schema()])
        n_tools = n_think = 0
        tool_log = []
        started = time.time()
        _ui = self.cfg.get("ui") or {}
        _v = _ui.get("turn_budget")
        try:
            budget = (float(_v) if _v not in (None, "")
                      else (SUBAGENT_BUDGET if is_sub else TURN_BUDGET))
        except (TypeError, ValueError):
            budget = SUBAGENT_BUDGET if is_sub else TURN_BUDGET
        seen_calls = {}     # 归一化调用键 → 次数(拦截"换个 max_chars 再读一遍"式空转)

        for rnd in range(rounds):
            if self.cancel.is_set():
                raise Cancelled()
            n = compact_messages(msgs)
            if n and not is_sub:
                self.slog.log("系统", "上下文超预算, 已压缩%d条历史" % n)
                self._emit_safe({"t": "notice",
                                 "text": "已自动压缩 %d 条历史工具结果(上下文接近上限)" % n})
            round_tools = tools_list
            call_msgs = msgs
            elapsed = time.time() - started
            if elapsed >= budget:
                # 时间预算到: 强制收尾, 保证用户一定拿到一个(哪怕是阶段性的)答案,
                # 而不是看着工具卡转 20 分钟最后手动停止。
                round_tools = []
                call_msgs = msgs + [{"role": "user", "content":
                    "[系统] 本轮已用 %.0f 秒, 时间预算已到。不要再调用任何工具, "
                    "立即基于已取得的信息回答用户的问题: 先给可执行的结论/配置步骤, "
                    "再简短列出尚未获取或未核实的部分。" % elapsed}]
                if not is_sub:
                    self.slog.log("系统", "时间预算到达(%.0fs), 强制收尾" % elapsed)
                    self._emit_safe({"t": "notice",
                                     "text": "本轮已达时间预算, 正在基于已取得的信息收尾"})
            elif max_rounds and rnd >= rounds - 1:
                round_tools = []            # 末步强制总结

            # ---- 流式回调: 主会话推UI, 子代理推节流心跳 ----
            beat = {"last": 0.0, "full": ""}

            def on_reason(full, _rnd=rnd):
                beat["full"] = full
                if is_sub:
                    now = time.time()
                    if now - beat["last"] >= 5.0:
                        beat["last"] = now
                        push("第%d/%d步 思考中(%d字)" % (_rnd + 1, rounds, len(full)))
                else:
                    self._emit_safe({"t": "think", "text": full[-2000:],
                                     "chars": len(full)})

            def on_content(full, _rnd=rnd):
                if is_sub:
                    push("第%d/%d步 生成回复…" % (_rnd + 1, rounds))
                else:
                    self._emit_safe({"t": "text", "text": full})

            msg, rlen, _ = L.stream_chat(self.profile, self.active, call_msgs, round_tools,
                                         cancel=self.cancel, on_reason=on_reason,
                                         on_content=on_content)
            n_think += rlen
            if not is_sub:
                if rlen:
                    self._emit_safe({"t": "think_end", "chars": rlen})
                    self.slog.log("思考", beat["full"][-12000:])

            tool_calls = msg.get("tool_calls")
            if tool_calls and not round_tools:
                # 强制收尾轮模型还想调工具: 不执行, 用已有文本收尾(防预算形同虚设)
                tool_calls = None
                msg["content"] = ((msg.get("content") or "")
                                  + "\n(时间预算已到, 已停止继续取数; 以上基于已取得的信息)")
            if not tool_calls:
                content = msg.get("content") or ""
                if not content.strip():
                    content = "(模型本轮输出为空。建议重新发送、换模型或简化输入)"
                if not is_sub:
                    vlog = self.sess["tool_log"][-50:] + tool_log
                    gate = verify.verify_round(vlog, msgs, content)
                    if gate["findings"]:
                        # 校验结果只作为独立的UI卡片(t=gate)与留痕日志, 不再拼进回答正文:
                        # 前端已有 gateBanner 渲染, 拼进正文会重复显示, 更会把系统旁注
                        # 写进对话历史, 诱导模型下一轮围绕"封禁/未通过校验"反复纠缠。
                        self.slog.log("校验", "passed=%s\n%s%s" % (
                            gate["passed"], "\n".join(
                                f["code"] + ": " + f["text"] for f in gate["findings"]),
                            "\n" + verify.gate_banner(gate)))
                        self._emit_safe({"t": "gate", "passed": gate["passed"],
                                         "findings": gate["findings"]})
                msgs.append({"role": "assistant", "content": content})
                if not is_sub:
                    self.slog.log("结论", content)
                return content, n_tools, n_think

            msgs.append({"role": "assistant", "content": msg.get("content"),
                         "tool_calls": tool_calls})

            # ---- 工具执行: 同批subagent并行(≤4), 其余顺序 ----
            def on_tool_start(fname, args_str, _rnd=rnd):
                """工具开始事件: 主会话推tool卡, 子代理推心跳。"""
                if is_sub:
                    push("第%d/%d步 调用工具 %s" % (_rnd + 1, rounds, fname))
                else:
                    self._emit_safe({"t": "tool", "name": fname,
                                     "args": str(args_str)[:300]})

            results = {}
            for i, tc in enumerate(tool_calls):
                k = tool_call_key(tc)
                seen_calls[k] = seen_calls.get(k, 0) + 1
                if seen_calls[k] > 1:
                    results[i] = ({"error": "重复调用被拦截: 同一工具+相同参数本轮已执行过, "
                                            "再执行一次不会得到新信息。请直接基于已有结果作答, "
                                            "或换参数(doc_read 用 offset 翻页 / find 关键词, 检索类换条件); "
                                            "不要用不同 max_chars 反复重读同一段。"}, 0.0)
            if not is_sub:
                sub_pos = [i for i, tc in enumerate(tool_calls)
                           if tc["function"]["name"] == "subagent" and i not in results]
                if len(sub_pos) >= 2:
                    parent_ctx = self._ctx()

                    def _exec_sub(tc):
                        self._tls.ctx = parent_ctx   # 子线程继承所属任务
                        return self._exec_tool(tc, on_tool_start)

                    with concurrent.futures.ThreadPoolExecutor(
                            max_workers=min(4, len(sub_pos))) as ex:
                        futs = {i: ex.submit(_exec_sub, tool_calls[i])
                                for i in sub_pos}
                    for i in sub_pos:
                        results[i] = futs[i].result()
            for i, tc in enumerate(tool_calls):
                if i not in results:
                    if self.cancel.is_set():
                        raise Cancelled()
                    results[i] = self._exec_tool(tc, on_tool_start, is_sub)

            # ---- 收尾: 消息先落, tool_done事件直接带本次结果预览 ----
            for i, tc in enumerate(tool_calls):
                fname = tc["function"]["name"]
                args_str = tc["function"].get("arguments") or "{}"
                result, dt = results[i]
                n_tools += 1
                ok = ("error" not in result) and ("refused" not in result)
                content = json.dumps(result, ensure_ascii=False, default=str)
                if len(content) > TOOL_RESULT_LIMIT:
                    content = (content[:TOOL_RESULT_LIMIT]
                               + "\n...[结果被引擎截断: 请改用分页/检索参数(如 doc_read 的 "
                                 "offset/find)缩小范围; 不要用更大的 max_chars 重读同一段, "
                                 "已有信息够就先作答]")
                self.slog.log("工具", "%s(%s)\n-> %s" % (fname, args_str, content[:8000]))
                if not is_sub:
                    self._emit_safe({"t": "tool_done", "name": fname, "ok": ok,
                                     "dt": round(dt, 1), "preview": content[:1500],
                                     "err": result.get("error") or result.get("reason")})
                tool_log.append({"name": fname, "args": args_str,
                                 "result_head": content[:2000]})
                self.sess["tool_log"].append(tool_log[-1])
                msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": content})

        return ("(已达单轮工具调用上限, 请缩小问题范围或新建会话后继续)",
                n_tools, n_think)

    def _exec_tool(self, tc, on_start=None, is_sub=False):
        """执行单个工具。返回 (result dict, dt)。on_start(fname, args_str)=开始事件回调。"""
        fname = tc["function"]["name"]
        args_str = tc["function"].get("arguments") or "{}"
        t0 = time.time()
        if self.cancel.is_set():
            raise Cancelled()
        if fname in EXEC_TOOL_NAMES and self.mode != "execute":
            return ({"error": "当前为只读查询模式, 处置类操作被拒绝。"
                             "请让用户切换到可执行模式"}, time.time() - t0)
        try:
            args = json.loads(args_str or "{}")
        except Exception as e:
            return {"error": "参数解析失败: %s" % e}, time.time() - t0
        if on_start:
            on_start(fname, args_str)
        if fname == "subagent":
            if is_sub:
                return {"error": "子代理不可再派生下级"}, time.time() - t0
            agent = args.get("agent", "explore")

            def push(text):
                self._emit_safe({"t": "sub", "text": "子代理%s · %s" % (agent, text)})

            try:
                result = self._dispatch_subagent(args.get("task", ""), agent, push)
            except Cancelled:
                raise
            except Exception as e:
                result = {"error": "子代理执行失败: %s: %s" % (type(e).__name__, e)}
            return result, time.time() - t0
        handler = HANDLERS.get(fname)
        if handler is None:
            return {"error": "未知工具 %r" % fname}, time.time() - t0
        try:
            result = handler(**args)
        except TypeError as e:
            result = {"error": "参数不匹配: %s" % e}
        except Cancelled:
            raise
        except Exception as e:
            result = {"error": "%s: %s" % (type(e).__name__, e)}
        if not isinstance(result, dict):
            result = {"result": result}
        return result, time.time() - t0

    # ---------- 子代理 ----------

    def _dispatch_subagent(self, task, agent, push):
        spec = SUBAGENTS.get(agent)
        if not spec:
            return {"error": "未知子代理 %r, 可用: %s" % (agent, ", ".join(sorted(SUBAGENTS)))}
        # 子代理走 toolset("query"), 提示词也必须按 query 重建: 主会话若是可执行模式,
        # 直接复用主提示词会让子代理去找根本不存在的 shell/run_python。
        sub_prompt = (build_system_prompt(self.cfg, self.persona, "query")
                      + "\n# 子代理限制: 永远只有只读工具面, 不得尝试写/执行类工具, "
                        "不得派生下级子代理; 任务做不完就如实报告已取得的信息与缺口。"
                      + spec.get("prompt_suffix", ""))
        sub_msgs = [{"role": "system", "content": sub_prompt},
                    {"role": "user", "content": str(task)[:8000]}]
        self.slog.log("子代理开始", "agent=%s steps=%d\n任务: %s"
                      % (agent, spec["steps"], str(task)[:500]))
        t0 = time.time()
        answer, n_tools, _ = self._agent_loop(sub_msgs, is_sub=True,
                                              max_rounds=spec["steps"], push=push)
        dt = time.time() - t0
        self.slog.log("子代理结束", "agent=%s 用时%.1fs 工具%d次" % (agent, dt, n_tools))
        out = {"agent": agent, "report": answer[:18000],
               "tools_used": n_tools, "elapsed": "%.1fs" % dt}
        if len(answer) > 18000:
            out["note"] = "报告超长已截断——批量任务请拆小委派或只要简表"
        return out
