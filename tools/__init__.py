# -*- coding: utf-8 -*-
"""工具注册表: TOOLS=全部工具schema, HANDLERS=执行器,
EXEC_TOOL_NAMES=处置类(需可执行模式), SEC_TOOL_NAMES=安全运营专用(persona过滤)。
新设备接入: 实现模块→注册schema/handler→caps.py补能力标签, 提示词零改动。

内置工具箱(builtin_tools 配置节, 设置中心「内置集成」可开关):
  fs_tools  文件浏览三件套(fs_ls/fs_glob/fs_grep)   file_ops  文件读写编辑(file_read/file_write/file_edit)
  shell     系统命令                                  web_tools  联网查询(web_fetch/web_search)
  todo      本地待办清单(todo_*)                      run_python 分析脚本(analysis.configure 沙箱联动)
关闭=从模型视野彻底移除(unregister), 开启=重新注册并按 caps 静态表回填能力标签。"""
from . import ngsoc, netbox, edr, ti, kb, fs, web, permissions
from . import rag, custom_api, analysis             # 配置驱动接入(RAG/自定义API/分析脚本)
from . import todo                                  # 内置轻量待办清单(纯本地)
from . import secops                                # 统一安全能力入口(设备无关: probe/search/detail)
from . import office                                # 文档处理(Office/WPS COM 自动化 + 进程清理)
from . import docs                                  # 文档读取(docx/xlsx/pptx/pdf 取文字, 只读)
import caps as _caps
from caps import CAPABILITIES, CAPABILITY_GROUPS, tools_with  # noqa: F401  能力标签(设备无关引用)

# 处置/写操作类工具(仅可执行模式解锁)
EXEC_TOOL_NAMES = {"ngsoc_dispose_alarms", "kb_add_entry", "shell", "file_write", "file_edit",
                   "run_python", "office_edit"}

# 安全运营专用工具(通用编码 persona 下隐藏, 避免模型在不相关场景误调生产接口)
SEC_TOOL_NAMES = {"ngsoc_search_alarms", "ngsoc_get_raw_alarms", "ngsoc_get_evidence",
                  "netbox_lookup", "edr_query", "threatbook_ip",
                  "kb_search", "kb_add_entry", "ngsoc_dispose_alarms",
                  "sec_probe", "sec_search", "sec_detail"}

# 内置工具分组: 开关名 → 工具名列表(设置中心「内置集成」按组开关)
BUILTIN_GROUPS = {
    "fs_tools":  ("fs_ls", "fs_glob", "fs_grep"),
    "file_ops":  ("file_read", "file_write", "file_edit"),
    "shell":     ("shell",),
    "web_tools": ("web_fetch", "web_search"),
    "todo":      ("todo_list", "todo_add", "todo_done", "todo_remove"),
}

# 内置工具 schema/handler 全表(apply_builtin_tools 重注册用, 与 TOOLS/HANDLERS 初值一致)
_BUILTIN_DEFS = {
    "fs_ls": (fs.SCHEMA_LS, fs.fs_ls),
    "fs_glob": (fs.SCHEMA_GLOB, fs.fs_glob),
    "fs_grep": (fs.SCHEMA_GREP, fs.fs_grep),
    "file_edit": (fs.SCHEMA_EDIT, fs.file_edit),
    "file_read": (permissions.SCHEMA_FILE_READ, permissions.file_read),
    "file_write": (permissions.SCHEMA_FILE_WRITE, permissions.file_write),
    "shell": (permissions.SCHEMA_SHELL, permissions.shell),
    "web_fetch": (web.SCHEMA_FETCH, web.web_fetch),
    "web_search": (web.SCHEMA_SEARCH, web.web_search),
    "todo_list": (todo.SCHEMA_LIST, todo.todo_list),
    "todo_add": (todo.SCHEMA_ADD, todo.todo_add),
    "todo_done": (todo.SCHEMA_DONE, todo.todo_done),
    "todo_remove": (todo.SCHEMA_REMOVE, todo.todo_remove),
}


# 连接器开关: 关闭的域 → 该域全部工具从模型视野移除(不卸载, 只是不可见)
_conn_off = set()


def apply_connectors(cfg):
    """按 connectors 配置计算"被关闭域"的工具集合; toolset() 里统一剔除。

    关闭只影响模型可见性(不卸载已加载的 MCP/插件), 避免反复断开重连;
    Office 走 office.configure 真注销, 内置工具箱走 builtin_tools 真注销。"""
    conn = cfg.get("connectors") if isinstance(cfg.get("connectors"), dict) else {}
    off = set()
    if conn.get("custom_api") is False:
        off |= {n for n in HANDLERS if n.startswith("api_")}
    if conn.get("rag") is False:
        off.add("rag_search")
    if conn.get("mcp") is False:
        off |= {n for n in HANDLERS if n.startswith("mcp__")}
    if conn.get("plugins") is False:
        try:
            import plugin_loader
            for names in (plugin_loader._plugin_tools or {}).values():
                off |= set(names or [])
        except Exception:
            pass
    _conn_off.clear()
    _conn_off.update(off)
    return sorted(off)


def register_tool(schema, handler, capabilities=None):
    """按配置注册工具(幂等: 同名先移除)。配置驱动的公开产品形态——
    设备集成类工具在 devices.json 无对应配置节时调用 unregister_tool 移除,
    模型视野里完全不出现。"""
    unregister_tool(schema["function"]["name"])
    TOOLS.append(schema)
    HANDLERS[schema["function"]["name"]] = handler
    if capabilities:
        _caps.CAPABILITIES[schema["function"]["name"]] = list(capabilities)


def unregister_tool(name):
    TOOLS[:] = [t for t in TOOLS if t["function"]["name"] != name]
    HANDLERS.pop(name, None)
    _caps.CAPABILITIES.pop(name, None)


def apply_builtin_tools(cfg):
    """按 builtin_tools 开关注册/注销内置工具箱(设置保存后热生效)。
    开启组重新注册并从 caps.STATIC_CAPABILITIES 回填能力标签
    (unregister 摘过标签, 重开时必须补回, 否则提示词能力清单缺项)。"""
    bt = cfg.get("builtin_tools") if isinstance(cfg.get("builtin_tools"), dict) else {}
    off = set()
    for gname, names in BUILTIN_GROUPS.items():
        if bt.get(gname, True) is False:
            off.update(names)
    for name in off:
        unregister_tool(name)
    for name, (schema, handler) in _BUILTIN_DEFS.items():
        if name in off:
            continue
        register_tool(schema, handler, capabilities=_caps.STATIC_CAPABILITIES.get(name))


def configure(cfg):
    ngsoc.configure(cfg)
    netbox.configure(cfg)
    edr.configure(cfg)
    ti.configure(cfg)
    kb.configure(cfg)
    fs.configure(cfg)
    web.configure(cfg)
    permissions.load_rules(cfg)
    rag.configure(cfg)            # 配置存在则注册rag_search, 否则注销(设置保存后热生效)
    custom_api.configure(cfg)     # 每个端点注册 api_<name>, 能力标签随配置走
    analysis.configure(cfg)       # run_python分析脚本工具(builtin_tools可开关)
    apply_builtin_tools(cfg)      # 内置工具箱分组开关(fs/file_ops/shell/web/todo)
    secops.configure(cfg)         # 项目=设备组 → sec_probe/sec_search/sec_detail(无设备则不注册)
    office.configure(cfg)         # 文档处理: 启动时检测 Office/WPS(只读注册表)
    docs.configure(cfg)           # 文档读取: 跟随「文档处理」连接器开关
    apply_connectors(cfg)         # 连接器开关: 关闭的域从模型视野移除


TOOLS = [
    # --- 安全研判 ---
    ngsoc.SCHEMA_SEARCH,
    ngsoc.SCHEMA_RAW,
    ngsoc.SCHEMA_EVIDENCE,
    netbox.SCHEMA,
    edr.SCHEMA,
    ti.SCHEMA,
    kb.SCHEMA,
    kb.SCHEMA_ADD,
    ngsoc.SCHEMA_DISPOSE,
    # --- 通用工作(OpenCode式) ---
    fs.SCHEMA_LS,
    fs.SCHEMA_GLOB,
    fs.SCHEMA_GREP,
    fs.SCHEMA_EDIT,
    web.SCHEMA_FETCH,
    web.SCHEMA_SEARCH,
    # --- 系统操作 ---
    permissions.SCHEMA_SHELL,
    permissions.SCHEMA_FILE_READ,
    permissions.SCHEMA_FILE_WRITE,
    # --- 文档处理(Office/WPS) ---
    office.SCHEMA_DETECT,
    office.SCHEMA_EDIT,
    docs.SCHEMA_READ,
    # --- 本地待办 ---
    todo.SCHEMA_LIST,
    todo.SCHEMA_ADD,
    todo.SCHEMA_DONE,
    todo.SCHEMA_REMOVE,
]


def toolset(mode, persona="ezcode", secops=None):
    """按模式与专家返回可用工具列表。
    mode: query=只读(写操作隐藏) / execute=全部;
    persona: ezcode=安全运营专家(全量) / 其它(如coder与自建专家, 默认隐藏安全运营专用工具);
    secops: 显式声明该专家开放安全工具面(自建专家 frontmatter tools: secops)"""
    drop = set(EXEC_TOOL_NAMES) if mode != "execute" else set()
    if persona != "ezcode" and not secops:
        drop |= SEC_TOOL_NAMES
    drop |= _conn_off          # 连接器里取消勾选的域
    return [t for t in TOOLS if t["function"]["name"] not in drop]


HANDLERS = {
    # --- 安全研判 ---
    "ngsoc_search_alarms": ngsoc.search_alarms,
    "ngsoc_get_raw_alarms": ngsoc.get_raw_alarms,
    "ngsoc_get_evidence": ngsoc.get_evidence,
    "netbox_lookup": netbox.lookup_ip,
    "edr_query": edr.query,
    "threatbook_ip": ti.threatbook_ip,
    "kb_search": kb.kb_search,
    "kb_add_entry": kb.kb_add_entry,
    "ngsoc_dispose_alarms": ngsoc.dispose_alarms,
    # --- 通用工作 ---
    "fs_ls": fs.fs_ls,
    "fs_glob": fs.fs_glob,
    "fs_grep": fs.fs_grep,
    "file_edit": fs.file_edit,
    "web_fetch": web.web_fetch,
    "web_search": web.web_search,
    # --- 系统操作 ---
    "shell": permissions.shell,
    "file_read": permissions.file_read,
    "file_write": permissions.file_write,
    # --- 文档处理(Office/WPS) ---
    "office_detect": office.detect,
    "office_edit": office.edit,
    # --- 本地待办 ---
    "todo_list": todo.todo_list,
    "todo_add": todo.todo_add,
    "todo_done": todo.todo_done,
    "todo_remove": todo.todo_remove,
}
