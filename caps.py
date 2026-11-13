# -*- coding: utf-8 -*-
"""能力标签注册表(纯数据, 零依赖) —— 提示词与校验门按"能力"引用工具, 不写死工具名。

为什么存在: 方法论(证据链/双确认/资产核查, 或 编码纪律/日常任务管理)是设备无关的,
具体设备(告警平台/主机侧/资产库)只是某种能力的当前提供者。接入新设备时:
  1. 在 tools/ 下实现工具模块, 注册 schema + handler(tools/__init__.py)
  2. 在本文件给新工具补 capability 标签(写入 STATIC_CAPABILITIES)
  3. 完成 —— 系统提示词的"能力清单"自动出现新工具, 校验门自动认账, 提示词零改动。

能力语义:
  --- 安全运营 ---
  device_probe  设备自检/权限探测(能否连上、token 能拉到什么)
  alert_search  告警/事件检索(按时间/站点/项目/编号拉取; sec_search 为跨设备聚合入口)
  evidence      报文级证据下钻(原始告警→报文解码)
  asset         IP资产归属核查(平台/业务/责任人)
  host          主机侧告警/主机详情(主机侧证据权威于流量推断)
  intel         外网IP威胁情报(多源)
  knowledge     研判知识库(判据/先例/指纹检索与沉淀)
  dispose       告警处置回写(写操作, 受双重确认)
  --- 通用工作台 ---
  office        文档处理(读 docx/xlsx/pptx/pdf 的文字; 调用本机 Office/WPS 整改, 自动清理进程)
  file_ops      文件与代码(浏览/检索/读写/精确编辑)
  code_exec     命令与脚本执行(shell/run_python)
  web           联网查询(公开资料搜索与原文抓取)
  todo          本地待办清单(记任务/列待办/标完成)
  write         写操作(需可执行模式+人工确认)
"""

STATIC_CAPABILITIES = {
    # --- 安全运营: 统一能力入口(设备无关) ---
    "sec_probe": ["device_probe"],
    "sec_search": ["alert_search"],
    "sec_detail": ["evidence"],
    # --- 安全运营: 检索 ---
    "ngsoc_search_alarms": ["alert_search"],
    "ngsoc_get_raw_alarms": ["evidence"],
    "ngsoc_get_evidence": ["evidence"],
    "netbox_lookup": ["asset"],
    "edr_query": ["host", "alert_search"],
    "threatbook_ip": ["intel"],
    # --- 安全运营: 知识库与处置 ---
    "kb_search": ["knowledge"],
    "kb_add_entry": ["knowledge", "write"],
    "ngsoc_dispose_alarms": ["dispose", "write"],
    # --- 文档处理(Office/WPS) ---
    "office_detect": ["office"],
    "office_edit": ["office", "write"],
    "doc_read": ["office"],
    # --- 通用工作台 ---
    "fs_ls": ["file_ops"],
    "fs_glob": ["file_ops"],
    "fs_grep": ["file_ops"],
    "file_read": ["file_ops"],
    "file_write": ["file_ops", "write"],
    "file_edit": ["file_ops", "write"],
    "shell": ["code_exec", "write"],
    "run_python": ["code_exec", "write"],
    "web_fetch": ["web"],
    "web_search": ["web"],
    "todo_list": ["todo"],
    "todo_add": ["todo"],
    "todo_done": ["todo"],
    "todo_remove": ["todo"],
}

# 运行期注册表: unregister_tool 会从中摘除, apply_builtin_tools 重新注册时按静态表回填
CAPABILITIES = dict(STATIC_CAPABILITIES)

# 校验门/提示词按能力分组时的展示顺序与中文标签
CAPABILITY_GROUPS = [
    ("文件与代码", "file_ops"),
    ("命令与脚本执行", "code_exec"),
    ("联网查询", "web"),
    ("本地待办清单", "todo"),
    ("文档处理(Office/WPS)", "office"),
    ("设备自检/权限探测", "device_probe"),
    ("告警/事件检索", "alert_search"),
    ("报文证据下钻", "evidence"),
    ("资产归属核查", "asset"),
    ("主机侧告警", "host"),
    ("威胁情报查询", "intel"),
    ("研判知识库", "knowledge"),
    ("处置回写", "dispose"),
]


def tools_with(capability):
    """返回具备某能力的全部工具名(按注册表推导, 新设备接入自动生效)。"""
    return sorted(n for n, caps in CAPABILITIES.items() if capability in caps)
