"use strict";
/* ═══════════ 界面语言(zh / en) ═══════════
   设计取舍:
   1. 不把中文原文改成 i18n key —— 源码里仍写中文, 可读性最好, 改文案不用同步两份;
   2. en 模式用"中文原文 → English"字典在 DOM 落地后统一替换(文本节点 + placeholder/
      title), 新增文案没翻译时自动回落中文, 不会出现 key 空白的破图;
   3. 只翻译界面容器(#sidebar/#topbar/#welcome/#composer/#overlay...), 绝不碰 #chat 里的
      模型输出与用户内容——那是数据, 不是界面;
   4. 语言存在 devices.json 的 ui.lang(引擎侧单一事实源), 前端启动时由 state().lang 恢复;
      同时写 localStorage 兜底, 避免引擎未就绪时首屏闪现中文。 */

const LANG_KEY = "ezcode-lang";

/* ---------- 字典: 中文原文(空白归一) → English ---------- */
const EN = {
  /* ── 侧边栏 / 顶栏 ── */
  "＋ 新建任务": "＋ New task",
  "新建任务": "New task",
  "任务列表": "Tasks",
  "工作区": "Workspace",
  "专家中心": "Experts",
  "浏览与新建专家": "Browse & create",
  "专家中心：浏览、切换与新建专家": "Expert center: browse, switch and create",
  "当前任务专家": "Current expert",
  "专家中心：选择/新建专家，决定本任务的工具面与提示词":
    "Expert center: pick or create an expert — it defines this task's tools and prompt",
  "技能": "Skills",
  "全局与项目级": "Global & project",
  "技能：全局与项目级技能管理": "Skills: global and project-level management",
  "连接器": "Connectors",
  "系统API · MCP · 插件": "System API · MCP · Plugins",
  "连接器：系统API / MCP / 插件接入": "Connectors: system API / MCP / plugin access",
  "设置中心": "Settings",
  "设置中心：模型、接入、扩展与偏好": "Settings: model, access, extensions, preferences",
  "新任务": "New task",
  "暂无任务，点上方「＋ 新建任务」开始": "No tasks yet — click “＋ New task” above to start",
  "空闲": "Idle",
  "运行中": "Running",
  "停止": "Stop",
  "发送": "Send",
  "关闭": "Close",
  "删除": "Delete",
  "编辑": "Edit",
  "启用": "Enable",
  "卸载": "Uninstall",
  "详情": "Details",
  "收起": "Collapse",
  "保存": "Save",
  "取消": "Cancel",
  "返回": "Back",
  "安装": "Install",
  "配置": "Configure",
  "结束": "Finish",

  /* ── 欢迎页 ── */
  "有什么可以帮你？": "What can I help you with?",
  "选一位专家开始：安全运营研判、编写项目代码，或处理日常事务。能力由你在设置中心按需接入。":
    "Pick an expert to start: security triage, project coding, or everyday tasks. Capabilities are connected on demand in Settings.",
  "@路径 可附加文件证据 · 写文件与跑命令需切到可执行模式":
    "@path attaches file evidence · file writes and commands need Execute mode",
  "正在连接研判引擎…": "Connecting to the engine…",
  "正在连接引擎": "Connecting to the engine",
  "已恢复会话上下文, 继续对话即可": "Session context restored — just keep chatting",

  /* ── 输入框 / 菜单 ── */
  "粘贴告警或描述任务，例如：研判今天 13:00 后未处理的告警…":
    "Paste an alert or describe a task, e.g. triage unhandled alerts since 13:00 today…",
  "直接下任务，例如：帮我写个Python脚本统计 logs 目录下今天的会话数…":
    "Just ask, e.g. write a Python script that counts today's sessions under logs…",
  "直接说事，例如：记一下明天上午开例会；帮我写一份周报框架…":
    "Just say it, e.g. note a meeting tomorrow morning; draft a weekly report outline…",
  "向「": "Describe a task to “",
  "」，直接描述任务即可": "” — just describe the task",
  "添加到对话": "Add to conversation",
  "附加文件证据": "Attach file evidence",
  "选择本地文件，按 @路径 引用": "Pick a local file, referenced as @path",
  "选择专家": "Choose expert",
  "切换或新建专家": "Switch or create an expert",
  "工具显示 · 紧凑": "Tool display · Compact",
  "切换外观": "Switch theme",
  "明亮 / 暗色": "Light / Dark",
  "模型 · 接入 · 扩展": "Model · Access · Extensions",
  "权限：只读查询 / 可执行": "Permission: read-only / execute",
  "执行权限": "Execution permission",
  "只读查询": "Read-only",
  "可执行": "Execute",
  "仅查询工具，写操作禁止": "Query tools only — writes blocked",
  "写文件 / 跑命令，逐次确认": "Write files / run commands, confirm each time",
  "上下文占用估算": "Context usage estimate",
  "上下文": "Context",
  "上下文占用": "Context usage",
  "超过 80% 自动压缩；压缩后会话记录仍完整留存":
    "Auto-compacts above 80%; the full session log is still kept",
  "立即压缩上下文": "Compact context now",
  "⇲ 立即压缩上下文": "⇲ Compact context now",
  "选择模型": "Choose model",
  "模型": "Model",
  "发送 (Enter)": "Send (Enter)",
  "回到最新消息": "Back to latest message",
  "↓ 回到最新": "↓ Latest",
  "停止本轮运行": "Stop this run",
  "Enter 发送 · Shift+Enter 换行 · @路径 附加文件证据 · 告警处置回写需切可执行模式":
    "Enter to send · Shift+Enter for newline · @path attaches evidence · alert write-back needs Execute mode",
  "Enter 发送 · Shift+Enter 换行 · @路径 附加文件 · 写文件/跑命令需切可执行模式":
    "Enter to send · Shift+Enter for newline · @path attaches a file · writes/commands need Execute mode",
  "Enter 发送 · Shift+Enter 换行 · @路径 附加材料 · 记待办/查资料无需可执行模式":
    "Enter to send · Shift+Enter for newline · @path attaches material · todos and research need no Execute mode",
  "Enter 发送 · Shift+Enter 换行 · @路径 附加文件证据 · 写操作需切可执行模式":
    "Enter to send · Shift+Enter for newline · @path attaches evidence · writes need Execute mode",
  "只做研判与建议 · 封禁/加白一律由你在防火墙手动执行 · 告警处置回写需逐次确认":
    "Analysis and advice only · blocking/allow-listing stays manual in your firewall · write-back needs per-action confirmation",
  "只读模式可直接干活 · 写文件/跑命令请切可执行模式 · 命令执行逐次弹窗确认":
    "Read-only mode is ready to work · switch to Execute for writes and commands · each command asks for confirmation",
  "写文档存盘需切可执行模式 · 待办清单只存本机 · 查资料只访问公开网页":
    "Saving documents needs Execute mode · todos stay on this machine · research only reads public pages",
  "只做分析与建议 · 高危操作一律由你确认后执行":
    "Analysis and advice only · risky actions run only after your confirmation",

  /* ── 专家文案 ── */
  "安全运营": "Security Ops",
  "通用编码": "Coding",
  "日常工作助手": "Daily Assistant",
  "专家": "Expert",
  "粘贴告警或直接说人话, EZcode 自动调用已接入的系统能力、跑行为链、检索知识库给出研判":
    "Paste an alert or just say it in plain words — EZcode calls your connected systems, walks the behavior chain and searches the knowledge base",
  "把任务交给 EZcode: 写代码、改文件、跑命令、分析数据。只读可直接干, 写文件/跑命令请先切到可执行模式":
    "Hand the task to EZcode: code, file edits, commands, data analysis. Read-only works right away; switch to Execute for writes and commands",
  "写作、总结、翻译、查资料、记待办都可以交给我; 试着说「记一下: 明天上午开例会」体验本地待办":
    "Writing, summaries, translation, research and todos are all fair game — try “note: team meeting tomorrow morning”",
  "内置专家": "Built-in experts",
  "自建专家": "My experts",
  "内置": "Built-in",
  "自建": "Custom",
  "使用中": "In use",
  "安全工具面": "Security toolset",
  "开始任务": "Start task",
  "· 项目级 agents/personas/ 与全局 ~/.ezcode/personas/ 均可":
    " · project agents/personas/ and global ~/.ezcode/personas/ both work",
  "专家中心：当前「": "Expert center: current “",

  /* ── 欢迎卡片 ── */
  "告警研判": "Alert triage",
  "批量分诊 · 行为链 · 处置建议": "Batch triage · Behavior chain · Advice",
  "帮我研判今天未处理的高危告警，先做批量分诊再给处置建议":
    "Triage today's unhandled high-severity alerts — batch triage first, then advice",
  "查知识库": "Search knowledge",
  "判据 · 历史处置 · 规范": "Criteria · Past handling · Policy",
  "检索知识库，总结这类事件的处置判据与历史案例":
    "Search the knowledge base and summarize criteria and past cases for this kind of event",
  "事件调查": "Incident investigation",
  "资产核查 · 情报比对 · 证据链": "Asset lookup · Intel · Evidence chain",
  "对这条告警做完整调查：资产归属、威胁情报比对、给出结论与置信度":
    "Investigate this alert fully: asset ownership, threat intel, verdict and confidence",
  "写代码 / 跑命令": "Code / run commands",
  "脚本 · 构建 · 自动化": "Scripts · Builds · Automation",
  "帮我写一个 Python 脚本统计日志并输出报表":
    "Write a Python script that summarizes logs into a report",
  "批量改文件": "Bulk file edits",
  "检索 · 精确编辑 · 验证": "Search · Precise edits · Verify",
  "在这个项目里找出所有 TODO 注释，列出来并给处理建议":
    "Find every TODO comment in this project, list them and suggest handling",
  "数据分析": "Data analysis",
  "解析 · 统计 · 可视化": "Parse · Stats · Charts",
  "帮我分析这份数据文件的字段分布和异常点":
    "Analyze the field distribution and outliers in this data file",
  "查公开资料": "Public research",
  "搜索 · 文档 · 技术方案": "Search · Docs · Solutions",
  "搜一下这个报错的常见原因和解决方案":
    "Look up common causes and fixes for this error",
  "写作 / 润色": "Writing / Polish",
  "邮件 · 周报 · 方案": "Email · Weekly · Proposals",
  "帮我写一份本周工作周报的框架，我口述要点你来组织":
    "Draft a weekly report outline — I'll dictate the points, you organize them",
  "记待办": "Add todo",
  "记任务 · 列清单 · 标完成": "Capture · List · Complete",
  "记一下待办：明天上午找张工对策略，周五前交周报":
    "Add todos: sync on policy with the engineer tomorrow morning; weekly report by Friday",
  "查资料": "Research",
  "搜索 · 整理 · 摘要": "Search · Organize · Summarize",
  "帮我查一下这个概念的公开资料，整理成一页速览":
    "Research this concept and turn it into a one-page brief",
  "翻译 / 总结": "Translate / Summarize",
  "中英互译 · 长文提炼": "ZH↔EN · Long-form digests",
  "把这段英文资料翻译成中文并总结成三个要点":
    "Translate this English material into Chinese and summarize three key points",
  "直接下任务": "Just assign a task",
  "按专家提示词工作": "Works by the expert prompt",
  "你好，先介绍一下你能帮我做什么、擅长什么":
    "Hi, tell me what you can do and what you're good at",
  "写与总结": "Write & summarize",
  "起草 · 润色 · 提炼": "Draft · Polish · Distill",
  "帮我把一段材料总结成要点": "Summarize a piece of material into key points",
  "记一下待办：": "Add a todo:",
  "查看已接入能力": "Connected capabilities",
  "系统API · MCP · 插件": "System API · MCP · Plugins",

  /* ── 设置中心: 导航 ── */
  "模型与运行": "Model & runtime",
  "能力接入": "Capabilities",
  "扩展与内容": "Extensions",
  "关于": "About",
  "大模型": "Model",
  "配置模型服务地址、密钥与默认使用的模型":
    "Model endpoint, API key and the default model",
  "运行偏好": "Preferences",
  "主题外观、工具调用显示、执行模式与上下文压缩":
    "Theme, tool call display, execution mode and context compaction",
  "内置能力与外部接入，勾选启用；也可以自己添加":
    "Built-in capabilities and external access — tick to enable, or add your own",
  "系统API接入": "System API access",
  "用你自己的系统命名，配置 url + key 即可接入":
    "Name it however your system does — just set url + key",
  "MCP服务器": "MCP servers",
  "通过标准协议接入外部工具服务": "Connect external tool services over the standard protocol",
  "全局与项目级研判思路，.md 文件即插即用":
    "Global and project-level playbooks — .md files plug right in",
  "插件": "Plugins",
  "安装、启停与卸载深度集成插件": "Install, toggle and remove deep-integration plugins",
  "RAG知识库": "RAG knowledge base",
  "接一个 HTTP 检索接口即可，无需模型支持 RAG":
    "One HTTP search endpoint is enough — the model needs no native RAG",
  "关于 EZcode": "About EZcode",
  "版本信息、开源协议与本机数据位置": "Version, license and local data location",
  "设置中心": "Settings",
  "配置模型、接入系统与扩展能力 · 保存后立即生效":
    "Configure models, systems and extensions · saved changes apply immediately",
  "导出当前会话": "Export this session",
  "保存本节": "Save section",
  "已保存并生效": "Saved and applied",
  "保存失败": "Save failed",
  "保存失败: 桥接异常": "Save failed: bridge error",
  "读取配置失败": "Failed to read config",
  "读取配置失败: 桥接异常": "Failed to read config: bridge error",

  /* ── 运行偏好 ── */
  "界面语言": "Interface language",
  "切换中文 / English，界面与模型回复语言一起切换":
    "Switch between 中文 and English — both the UI and the model's reply language change",
  "外观主题": "Appearance",
  "切换明亮 / 暗色界面，选择保存在本机": "Light or dark UI, stored on this machine",
  "☀ 明亮": "☀ Light",
  "🌙 暗色": "🌙 Dark",
  "工具调用显示": "Tool call display",
  "只显示「调了什么工具」，不展示返回内容与密钥等敏感数据；隐藏档运行时顶部仍有进度提示":
    "Shows which tool ran, never the returned data or secrets; Hidden still shows progress at the top",
  "执行模式": "Execution mode",
  "只读查询可直接干活；写文件与跑命令需切到可执行模式并逐次确认":
    "Read-only runs right away; writes and commands need Execute mode with per-action confirmation",
  "上下文压缩": "Context compaction",
  "历史对话 → AI 纪要 + 保留最近原文；水位超过 80% 自动触发，会话记录仍完整留存":
    "History → AI summary + recent originals; auto-triggered above 80%, full log kept",
  "这些偏好只对当前这台机器生效，不写入发布包，也不会同步到任何服务器。":
    "These preferences apply to this machine only; nothing is written into release packages or synced to any server.",
  "偏好已更新": "Preference updated",
  "正在压缩上下文…(完成后对话流里会有提示)":
    "Compacting context… (a note will appear in the chat when done)",
  "压缩失败": "Compaction failed",
  "压缩失败: 桥接异常": "Compaction failed: bridge error",
  "切换失败: 桥接异常": "Switch failed: bridge error",
  "运行中不能切模式": "Can't switch mode while running",
  "运行中不能切换模式": "Can't switch mode while running",
  "已切换到可执行模式(写操作仍需逐次确认)":
    "Switched to Execute mode (writes still need confirmation)",
  "已切换到只读查询模式": "Switched to Read-only mode",

  /* ── 大模型 ── */
  "配置名称": "Profile name",
  "删除该配置": "Delete this profile",
  "新配置": "New profile",
  "至少保留一个配置": "Keep at least one profile",
  "名称已存在": "Name already exists",
  "读取模型清单失败": "Failed to read model list",
  "尚未配置模型，请到设置中心「大模型」添加": "No model yet — add one in Settings › Model",
  "运行中不能切模型": "Can't switch model while running",
  "⚙ <b>管理模型…</b>": "⚙ <b>Manage models…</b>",

  /* ── 连接器 ── */
  "文件与代码": "Files & code",
  "浏览目录、按内容检索、读写与精确编辑文件":
    "Browse directories, search by content, read/write and edit files precisely",
  "联网查询": "Web search",
  "搜索公开资料并抓取原文（自带内网地址拦截）":
    "Search public sources and fetch originals (internal addresses blocked)",
  "分析脚本": "Analysis scripts",
  "直接跑一段分析代码，省去先写文件再执行":
    "Run a snippet directly instead of writing a file first",
  "本地待办清单": "Local todos",
  "记任务 / 列待办，纯本机存储、跨会话":
    "Capture and list tasks — local storage, across sessions",
  "Office / WPS 文档处理": "Office / WPS documents",
  "调用本机 Office/WPS 整改 Word/Excel/PPT，完成后自动清理进程":
    "Drive local Office/WPS to fix Word/Excel/PPT, then clean up the processes it started",
  "系统 API 接入": "System API access",
  "用你的系统命名接入，填 url + key 即可": "Connect under your own system name — just url + key",
  "MCP 服务器": "MCP servers",
  "内置模板一键添加，也支持自定义 stdio / HTTP": "Add built-in templates in one click, or custom stdio / HTTP",
  "插件": "Plugins",
  "深度集成 + 把处理思路一起注入": "Deep integration plus injected playbook",
  "自定义提示词片段，勾选后注入系统提示词":
    "Custom prompt fragments, injected once ticked",
  "接一个 HTTP 检索接口即可": "A single HTTP search endpoint is enough",
  "已配置": "Configured",
  "未配置": "Not configured",
  "已停用": "Disabled",
  "勾选状态立即生效，写入本机配置": "Ticks apply immediately and are written to local config",
  "可执行": "Execute",
  "（未启用）": "(disabled)",

  /* ── 系统API接入 ── */
  "告警检索系统": "Alert search system",
  "主机侧告警系统": "Host-side alert system",
  "资产库(IP归属核查)": "Asset store (IP ownership)",
  "深度接入（代码内置解析器，地址与密钥由你填写）：流量侧告警检索、报文下钻与处置回写":
    "Deep integration (built-in parser; you supply address and key): traffic alert search, packet drill-down and write-back",
  "深度接入（代码内置解析器，项目与 Token 由你填写）：主机防护类告警查询与主机证据下钻":
    "Deep integration (built-in parser; you supply project and token): host alert queries and host evidence drill-down",
  "深度接入（代码内置解析器，地址与密钥由你填写）：IP 归属与资产核查":
    "Deep integration (built-in parser; you supply address and key): IP ownership and asset lookup",
  "内联站点": "Inline site",
  "站点名称(对话里引用)": "Site name (used in chat)",
  "删除站点": "Delete site",
  "检索别名(可选)": "Search alias (optional)",
  "校验SSL证书": "Verify SSL certificate",
  "内网自签环境请关闭": "Turn off for internal self-signed setups",
  "客户端模块目录": "Client module directory",
  "外部API客户端所在目录, 可留空": "Directory of the external API client; may be empty",
  "项目名(自己命名)": "Project name (your own)",
  "访问令牌": "Access token",
  "接口地址": "Endpoint URL",
  "自签环境请关闭": "Turn off for self-signed setups",
  "名称(注册为 api_<名称>)": "Name (registered as api_<name>)",
  "给模型看的一句话用途说明": "One line telling the model what it is for",
  "可选, Bearer鉴权": "Optional, Bearer auth",
  "knowledge / asset / intel / host, 逗号分隔": "knowledge / asset / intel / host, comma separated",
  "新增自定义接入": "Add custom access",
  "自检中…": "Probing…",
  "自检全部": "Probe all",
  "可用": "Ready",
  "失败": "Failed",
  "逐设备校验 token 权限与可达性": "Check token permissions and reachability device by device",
  "就绪": "Ready",
  "待映射": "Needs mapping",
  "通用 HTTP 接入：靠字段映射归一化，未配映射前无法参与聚合检索":
    "Generic HTTP access normalized by field mapping; without a mapping it can't join aggregated search",
  "主机侧专用适配器：支持主机告警查询与进程/文件类证据下钻":
    "Host adapter: host alert queries and process/file evidence drill-down",
  "流量侧专用适配器：支持告警检索、报文下钻与处置回写":
    "Traffic adapter: alert search, packet drill-down and write-back",
  "有文档": "Doc attached",
  "文档": "Doc",
  "自动适配": "Auto-map",
  "生成中…": "Generating…",
  "正在取样并让模型生成字段映射，请稍候…":
    "Sampling data and asking the model for a field mapping, hold on…",
  "自动适配失败": "Auto-mapping failed",
  "自动适配异常:": "Auto-mapping error: ",
  "已用真实样本校验通过": "Verified against a real sample",
  "保存映射并生效": "Save mapping",
  "映射已保存并生效": "Mapping saved and applied",
  "映射 JSON 解析失败:": "Failed to parse mapping JSON: ",
  "保存中…": "Saving…",
  "保存文档": "Save doc",
  "接口文档已保存": "API doc saved",
  "(无预览)": "(no preview)",
  "(无返回)": "(no output)",
  "预置接入已保存并生效": "Preset access saved and applied",
  "设备组已写入配置；原站点与项目配置保留但不再参与聚合":
    "Device group written to config; old site and project settings are kept but no longer aggregate",
  "移除预置接入": "Remove preset access",
  "保存预置接入": "Save preset access",

  /* ── MCP ── */
  "内置模板": "Built-in templates",
  "自定义服务器": "Custom server",
  "未添加": "Not added",
  "(未命名)": "(unnamed)",
  "删除该服务器": "Delete this server",
  "工具注册为 mcp__名称__工具": "Tools register as mcp__name__tool",
  "http型留空; stdio型: npx -y xxx-server": "empty for http; for stdio: npx -y xxx-server",
  "还没有添加任何 MCP 服务器。": "No MCP servers added yet.",
  "，点「保存本节」生效": " — click “Save section” to apply",
  "删除 MCP 服务器「": "Delete MCP server “",
  "」？保存后其工具将下线。": "”? Its tools go offline after saving.",
  "鉴权头不是合法 JSON": "Auth header is not valid JSON",
  "连接中…": "Connecting…",
  "✗ 桥接异常": "✗ Bridge error",
  "第三方库文档与代码示例检索，无需密钥": "Library docs and code samples, no key needed",
  "知识图谱长期记忆，跨会话记住实体与关系": "Knowledge-graph memory that remembers entities across sessions",
  "查询本地 SQLite 数据库，添加后改成库文件路径": "Query a local SQLite database; point it at your file after adding",
  "查询 PostgreSQL 数据库，添加后改连接串": "Query PostgreSQL; edit the connection string after adding",
  "仓库代码 / Issue / PR 检索，需在鉴权头填 GitHub Token":
    "Search repos / issues / PRs; put a GitHub token in the auth header",
  "真实浏览器操作：打开页面、点击、填表、截图": "Real browser: open pages, click, fill forms, screenshot",
  "结构化分步推理，复杂问题拆解时更稳": "Structured step-by-step reasoning for complex problems",

  /* ── 技能 ── */
  "技能": "Skills",
  "暂无技能": "No skills yet",
  "新技能名称(如: 周报格式规范)": "New skill name (e.g. Weekly report format)",
  "＋ 新建技能": "＋ New skill",
  "请填写技能名称": "Enter a skill name",
  "在这里写技能内容(提示词片段), 保存后下一轮对话生效。":
    "Write the skill body here (a prompt fragment); it takes effect next turn.",
  "技能已创建": "Skill created",
  "技能已保存, 下一轮生效": "Skill saved — effective next turn",
  "创建失败": "Create failed",
  "读取技能失败": "Failed to read skills",
  "加载技能清单…": "Loading skills…",
  "名称/说明的修改也在保存内容时生效": "Name and description changes are saved with the body",
  "保存内容": "Save body",
  "全局": "Global",
  "项目级": "Project",

  /* ── 插件 ── */
  "已加载": "Loaded",
  "未加载(禁用或加载失败)": "Not loaded (disabled or failed)",
  "加载插件清单…": "Loading plugins…",
  "读取插件失败": "Failed to read plugins",
  "暂无插件——安装一个试试": "No plugins yet — install one to try",
  "📁 从文件安装…": "📁 Install from file…",
  "✏️ 粘贴代码安装": "✏️ Paste code to install",
  "插件文件名(如 告警速判)": "Plugin file name (e.g. quick-triage)",
  "未选择": "Nothing selected",
  "安装中…": "Installing…",
  "安装失败": "Install failed",
  "已安装": "Installed",
  "请填文件名和代码": "Provide a file name and code",
  "已启用": "Enabled",
  "已禁用": "Disabled",
  "卸载失败": "Uninstall failed",
  "已卸载": "Uninstalled",
  "操作失败": "Action failed",

  /* ── RAG ── */
  "注册检索工具后，模型才能在需要时自己调用":
    "Register the search tool so the model can call it when needed",
  "检索接口地址": "Search endpoint URL",
  "查询字段名": "Query field name",
  "默认 query；接口要 q / question / keyword 就改这里":
    "Defaults to query — set q / question / keyword if your API expects that",
  "请求方式": "HTTP method",
  "可选，Bearer 鉴权": "Optional, Bearer auth",
  "附带 top_k": "Send top_k",
  "默认不发；接口支持条数限制再打开": "Off by default; enable if the API supports a result limit",
  "校验SSL": "Verify SSL",
  "内网自签证书请保持关闭": "Keep off for internal self-signed certificates",
  "超时(秒)": "Timeout (s)",
  "测试中…": "Testing…",
  "测试检索": "Test search",
  "失败：": "Failed: ",
  "（内容为空，检查字段名或检索库）": "(empty result — check the field name or the index)",

  /* ── 关于 ── */
  "版本 v44 · Windows 单文件安装版": "v44 · Windows single-file installer",
  "产品定位": "What it is",
  "本地优先的多场景桌面 AI 应用：安全运营研判、编写项目代码、日常工作助手。模型只负责推理，工具能力与外部系统由你自己在设置中心配置。":
    "A local-first desktop AI app for security triage, project coding and daily work. The model only reasons; tools and external systems are configured by you in Settings.",
  "开源协议": "License",
  "MIT License · 可自由使用、修改与分发": "MIT License · free to use, modify and distribute",
  "本机数据": "Local data",
  "配置、会话、技能、插件全部留在安装目录与用户目录，不上传任何服务器；发布包内不含凭据。":
    "Config, sessions, skills and plugins stay in the install and user directories; nothing is uploaded, and release packages contain no credentials.",
  "能力组织": "How it fits together",
  "专家决定提示词与工具面；技能沉淀工作方法；连接器接入系统 API / MCP / 插件；内置工具箱按组开关。":
    "Experts define the prompt and tool surface; skills capture working methods; connectors bring in system APIs, MCP and plugins; the built-in toolbox switches by group.",
  "查看接入配置": "View access config",
  "未做代码签名，首次运行 Windows SmartScreen 可能提示「未知发布者」，选择「仍要运行」即可。":
    "The build is unsigned, so SmartScreen may warn about an unknown publisher on first run — choose “Run anyway”.",
  "版本": "Version",

  /* ── 专家中心弹窗 ── */
  "新建专家": "New expert",
  "保存到 agents/personas/*.md · 立即生效 · 所有任务可用":
    "Saved to agents/personas/*.md · applies immediately · available in all tasks",
  "专家名称": "Expert name",
  "例如：告警分诊助手": "e.g. Alert triage helper",
  "图标(可选)": "Icon (optional)",
  "一个 emoji": "a single emoji",
  "一句话描述": "One-line description",
  "说明这位专家做什么": "What this expert does",
  "工具面": "Tool surface",
  "通用工具(编码/文件/网页等)": "General tools (coding/files/web…)",
  "含安全运营工具面(告警检索/主机侧/资产/情报)":
    "Include the security toolset (alert search / host / assets / intel)",
  "系统提示词": "System prompt",
  "保存专家": "Save expert",
  "已创建专家": "Expert created",
  "专家名称与系统提示词不能为空": "Expert name and system prompt are required",
  "读取专家清单失败": "Failed to read experts",
  "读取专家清单失败: 桥接异常": "Failed to read experts: bridge error",
  "运行中不能切换专家, 请先停止": "Can't switch expert while running — stop first",
  "运行中不能新建任务, 请先停止": "Can't create a task while running — stop first",
  "切换失败": "Switch failed",
  "已切换专家 · 写操作回退只读": "Expert switched · writes reset to read-only",
  "确认删除?": "Delete this?",
  "删除失败": "Delete failed",
  "删除失败: 桥接异常": "Delete failed: bridge error",

  /* ── 会话 / 消息 ── */
  "已切换任务": "Task switched",
  "停止这个任务": "Stop this task",
  "已请求停止该任务": "Stop requested for that task",
  /* ── 后台任务: 切走后仍继续运行 ── */
  "后台运行中": "Running in background",
  "有新回答": "New answer",
  "待确认": "Awaiting confirmation",
  "后台任务继续运行中…": "Background task still running…",
  "来自后台任务：": "From background task: ",
  "确认后该任务继续运行": "Confirm to let that task continue",
  "思考": "Thinking",
  "生成回复": "Replying",
  "工具": "Tool",
  "子代理运行中": "Subagent running",
  "已删除": "Deleted",
  "本轮仍在运行——输入已保留, 回复结束后再发送; 需中止请点「停止」":
    "This run is still going — your input is kept, send it when the reply finishes; click Stop to abort",
  "已请求中止": "Stop requested",
  "发送失败": "Send failed",
  "发送失败: 与引擎的桥接异常": "Send failed: bridge error",
  "已复制全文": "Full text copied",
  "代码已复制": "Code copied",
  "工具调用显示:": "Tool call display: ",
  "紧凑(一行, 点击展开)": "Compact (one line, click to expand)",
  "紧凑": "Compact",
  "详细(完整卡片)": "Detailed (full cards)",
  "详细": "Detailed",
  "隐藏(顶部仅运行提示)": "Hidden (top progress only)",
  "隐藏": "Hidden",
  "点击查看调用概要(不显示返回内容)": "Click to see call summary (no returned data)",
  "无返回": "No output",
  "流程校验通过(确定性校验, 非模型自评)": "Flow check passed (deterministic, not self-graded)",
  "流程校验未通过": "Flow check did not pass",
  "未知错误": "Unknown error",
  "已中止本轮": "This run was stopped",
  "思考中…": "Thinking…",
  "用时": "Elapsed",
  "已切换到暗色主题": "Switched to dark theme",
  "已切换到明亮主题": "Switched to light theme",
  "在 @ 后粘贴文件绝对路径即可附加": "Paste an absolute path after @ to attach it",
  "已附加文件证据": "File evidence attached",
  "文件选择失败: 桥接异常": "File picker failed: bridge error",
  "展开当前任务专家": "Show the current task expert",
  "收起当前任务专家": "Hide the current task expert",
  "工具显示": "Tool display",
  "正在连接研判引擎": "Connecting to the engine",
  /* ── 表单标签 / 栅格 / 弹窗(设置中心各处) ── */
  "名称": "Name",
  "模型名": "Model name",
  "上下文": "Context",
  "默认": "Default",
  "＋ 新增模型配置": "＋ Add model profile",
  "128000(可选)": "128000 (optional)",
  "项目与设备组": "Projects & device groups",
  "＋ 新增项目": "＋ Add project",
  "＋ 新增站点": "＋ Add site",
  "台设备": "devices",
  "项目": "Project",
  "设备": "Device",
  "状态": "Status",
  "条数": "Count",
  "说明": "Notes",
  "站点代码": "Site code",
  "服务器": "Server",
  "API版本": "API version",
  "能力标签": "Capability tags",
  "超时秒": "Timeout (s)",
  "旧版接口": "Legacy API",
  "新版接口": "New API",
  "Access Key": "Access Key",
  "stdio命令": "stdio command",
  "鉴权头": "Auth header",
  "传输": "Transport",
  "测试连接": "Test connection",
  "＋ 自定义服务器": "＋ Custom server",
  "勾选即添加并启用": "Tick to add and enable",
  "已添加的服务器": "Added servers",
  "用当前填写的地址真发一次请求，不保存配置":
    "Send one real request with these values — nothing is saved",
  "字段映射草案 ·": "Field mapping draft · ",
  "映射 JSON": "Mapping JSON",
  "归一化预览（前": "Normalized preview (first ",
  "权限确认 ·": "Permission · ",
  "拒绝": "Deny",
  "本会话总是允许": "Always allow this session",
  "允许这一次": "Allow once",
  "状态读取中…": "Reading status…",
  "已启用，点击停用": "Enabled — click to disable",
  "已停用，点击启用": "Disabled — click to enable",
  "文件 · 脚本 · 数据分析": "Files · Scripts · Data analysis",
  "列出当前已接入的系统能力，并说明每类能查什么":
    "List the connected system capabilities and what each can query",
  "专家决定任务的系统提示词与工具面 · 自建专家保存在 agents/personas/":
    "Experts define the task's system prompt and tool surface · custom experts live in agents/personas/",
  "名称 / 描述 / 系统提示词，保存即生效，可随时删除":
    "Name / description / system prompt — saved instantly, deletable anytime",
  "以下站点从外部清单文件导入:": "These sites were imported from an external list:",
  "写入配置, 该外部文件不再被使用。": "and written into the config; that external file is no longer used.",
  "接入任何外部系统": "Connect any external system",
  "次": "times",
  "成功": "Success",
  "字符": "chars",
  "个工具": "tools",
  "条消息 ·": "messages · ",
  "个启用 / 共": "enabled / total ",
  "工具:": "Tools:",
  "测试异常：": "Test error: ",
  "保存异常:": "Save error: ",
  "失败：": "Failed: ",
  "ms，返回": " ms, returned ",
  "✓ 连通，": "✓ Connected, ",
  "个工具：": " tools: ",
  "添加文件与快捷操作": "Add files and quick actions",
  "工具显示 · 紧凑": "Tool display · Compact",
  "工具显示 · 详细": "Tool display · Detailed",
  "工具显示 · 隐藏": "Tool display · Hidden",
  "工具调用只显示概要, 不展示返回内容(点击切换档位): 隐藏/紧凑/详细":
    "Tool calls show a summary only, no returned data (click to switch): hidden / compact / detailed",
  "RAG 知识库": "RAG knowledge base",
  "＋ 新增自定义接入": "＋ Add custom access",
  "点击添加并启用": "Click to add and enable",
  "点击停用": "Click to disable",
  "删除此接入的全部配置(保存后生效, 对话工具随配置移除)":
    "Remove all settings for this integration (applies on save; its tools leave the chat)",
  "粘贴该设备的接口文档：告警列表接口的请求参数、响应字段、鉴权方式（只要相关章节即可）。仅在「自动适配」时发给当前模型，保存在本机配置，不上传。":
    "Paste this device's API doc — request params, response fields and auth for the alert list endpoint (only the relevant sections). It is sent to the current model only during Auto-map, stored locally, never uploaded.",
  "JSON, 如 {\"Authorization\":\"Bearer xx\"}": "JSON, e.g. {\"Authorization\":\"Bearer xx\"}",
  "复制全文": "Copy full text",
  "stdio (本地命令行)": "stdio (local command)",
  "HTTP地址": "HTTP URL",
  "GET（query 参数）": "GET (query params)",
  "POST（JSON body）": "POST (JSON body)",
  "文档会随「自动适配」一起给模型；文档与真实样本冲突时以样本为准。":
    "The doc goes to the model together with Auto-map; if doc and real samples disagree, real samples win.",
  "运行中…": "Running…",
  "(运行中…)": "(running…)",
  "个": "",
  "条）": " rows)",
  "保存后将以\"内联站点\"写入配置, 该外部文件不再被使用。":
    "On save these become inline sites in the config; that external file is no longer used.",

  /* ── 自动更新 ── */
  "版本更新": "Updates",
  "启动时自动检查 GitHub 与国内服务器上的新版本": "Check GitHub and your local server for new versions on startup",
  "检查更新": "Check for updates",
  "检查中…": "Checking…",
  "当前版本": "Current version",
  "新版本": "New version",
  "必须升级": "Required",
  "已启用": "Enabled",
  "已停用": "Disabled",
  "未检查": "Not checked",
  "已是最新版本": "You are up to date",
  "正在检查更新…": "Checking for updates…",
  "启动检查已关闭，可手动检查": "Startup check is off — you can still check manually",
  "未启用任何更新源": "Update sources are not configured yet — fill them in below",
  "检查失败：网络不可达或更新源未配置": "Check failed: network unreachable or sources not configured",
  "此版本已被忽略": "This version is ignored",
  "检查失败: ": "Check failed: ",
  "下载失败: ": "Download failed: ",
  "正在下载": "Downloading",
  "下载完成，可安装": "Downloaded — ready to install",
  "下载更新": "Download update",
  "重新下载": "Re-download",
  "安装并重启": "Install & restart",
  "再次点击确认（将关闭 EZcode）": "Click again to confirm (EZcode will close)",
  "打开下载目录": "Open download folder",
  "忽略此版本": "Skip this version",
  "更新源": "Update sources",
  "可同时启用多条，客户端并行检查并自动采用最高版本":
    "Enable several at once — all are checked in parallel and the highest version wins",
  "启用自动更新": "Enable automatic updates",
  "启动时自动检查": "Check on startup",
  "保存更新设置": "Save update settings",
  "正在读取更新状态…": "Loading update status…",
  "未配置更新源": "No update sources configured",
  "仓库 owner/repo": "Repository owner/repo",
  "下载镜像（可选，国内加速）": "Download mirror (optional, for faster access in China)",
  "version.json 地址（HTTPS / 内网共享 / 本地文件）":
    "version.json URL (HTTPS / intranet share / local file)",
  "sha256 校验已启用": "sha256 verification enabled",
  "未声明 sha256，将跳过完整性校验": "No sha256 declared — integrity check will be skipped",
  "尚未检查": "Not checked yet",
  "发布于": "Released",
  "安装包": "Package",
  "更新只读取版本清单并下载安装包，不会上传本机配置、会话或凭据；升级安装会保留 devices.json 与判据库。":
    "Updates only read a version manifest and download an installer. Your config, sessions and credentials are never uploaded; upgrades keep devices.json and the criteria library.",
  "已忽略此版本": "Version skipped",
  "更新设置已保存，正在重新检查…": "Update settings saved — rechecking…",
  "EZcode 即将关闭并自动安装新版本": "EZcode will close and install the new version",
  "检查更新失败: 桥接异常": "Update check failed: bridge error",
  "下载失败: 桥接异常": "Download failed: bridge error",
  "启动安装失败: 桥接异常": "Install failed: bridge error",
  "保存失败: 桥接异常": "Save failed: bridge error",

  /* ── 大模型: 厂商快捷接入 ── */
  "快速接入": "Quick setup",
  "自定义配置": "Custom configuration",
  "接口地址与模型名自己填，兼容任何 OpenAI 协议服务":
    "Fill in the endpoint and model yourself — works with any OpenAI-compatible service",
  "加载模型厂商…": "Loading providers…",
  "当前默认": "Default",
  "已接入": "Connected",
  "填 Key 即用": "Just add a key",
  "接入": "Connect",
  "收起": "Collapse",
  "模型": "Model",
  "其他模型(手动输入)": "Other model (type it)",
  "模型名，可手动填写": "Model id, e.g. gpt-6.1-sol",
  "测试连接": "Test connection",
  "接入并使用": "Connect and use",
  "已接入并设为默认模型": "Connected and set as default",
  "请先填写 API Key": "Please enter the API key first",
  "请先选择或填写模型名": "Please choose or type a model id first",
  "测试中…": "Testing…",
  "连接正常": "Connection OK",
  "连接失败": "Connection failed",
  "默认": "Default",
  "配置名称": "Profile name",
  "删除该配置": "Delete this profile",
  "接口地址": "Endpoint",
  "模型名": "Model",
  "上下文": "Context",
  "128000(可选)": "128000 (optional)",
  "新增模型配置": "Add profile",
  "至少保留一个配置": "Keep at least one profile",
  "名称已存在": "Name already exists",
  "新配置": "New profile",

  /* ── 完成提示 ── */
  "完成提示": "Completion alerts",
  "回答结束时在右下角弹一张提示卡；选「自动」只在你切到别的任务或窗口不在前台时才提醒":
    "Show a card at the bottom-right when an answer finishes. Auto only alerts you when you switched tasks or the window is in the background.",
  "自动": "Auto",
  "总是": "Always",
  "关闭": "Off"
};

/* ---------- 动态文案规则(带参数, 无法用整串精确匹配) ---------- */
const EN_RULES = [
  /* 注意: 替换函数的第一个参数是整段匹配文本(String.replace 语义),
     分组要取后面的参数 —— 写成 m => m[1] 会拿到"匹配串的第2个字符" */
  [/^已导出: (.*)$/, (m, a) => "Exported: " + a],
  [/^已切换: (.*)$/, (m, a) => "Switched to: " + a],
  [/^已启用 (.+)$/, (m, a) => "Enabled " + a],
  [/^已停用 (.+)$/, (m, a) => "Disabled " + a],
  [/^(.+?)s 未取到结果$/, (m, a) => "No result after " + a + "s"],
  [/^用时 (.+?)s · 工具 (\d+) 次$/, (m, a, b) => "Elapsed " + a + "s · " + b + " tool calls"],
  [/^思考中… (\d+)字$/, (m, a) => "Thinking… " + a + " chars"],
  [/^✦ 思考过程\((\d+)字\)$/, (m, a) => "✦ Reasoning (" + a + " chars)"],
  [/^(\d+)条消息 · (.+)$/, (m, a, b) => a + " messages · " + b],
  [/^(\d+) 个接入$/, (m, a) => a + " integrations"],
  [/^(\d+) 个服务器$/, (m, a) => a + " servers"],
  [/^(\d+) 个插件$/, (m, a) => a + " plugins"],
  [/^(\d+) 个技能$/, (m, a) => a + " skills"],
  [/^(\d+) 条待办$/, (m, a) => a + " todos"],
  [/^共 (\d+) 条$/, (m, a) => a + " in total"],
  [/^共 (\d+) 条：误报(\d+)\/待确认(\d+)$/, (m, a, b, c) => a + " total: false positives " + b + " / to confirm " + c],
  [/^向「(.+)」描述任务…$/, (m, a) => "Describe a task to “" + a + "”…"],
  [/^已切换到「(.+)」，直接描述任务即可$/, (m, a) => "Switched to “" + a + "” — just describe the task"],
  [/^专家中心：当前「(.+)」，点击选择或新建专家$/, (m, a) => "Expert center: current “" + a + "” — click to switch or create"],
  [/^(\d+)% · 点击查看 \/ 压缩$/, (m, a) => a + "% · click to view / compact"],
  [/^上下文占用约 (\d+)%$/, (m, a) => "Context usage ~" + a + "%"],
  [/^(.+)个接入$/, (m, a) => a + " integrations"],
  [/^(.+)个工具$/, (m, a) => a + " tools"],
  [/^(\d+) 台设备$/, (m, a) => a + " devices"],
  [/^(\d+) 个启用 \/ 共 (\d+) 个$/, (m, a, b) => a + " enabled / " + b + " total"],
  [/^工具: (.+)$/, (m, a) => "Tools: " + a],
  [/^上下文占用约 (\d+)% · 点击查看 \/ 压缩$/, (m, a) => "Context usage ~" + a + "% · click to view / compact"],
  [/^用时 (.+?)s · 工具 (\d+) 次 · 思考 (\d+) 字$/, (m, a, b, c) => "Elapsed " + a + "s · " + b + " tool calls · " + c + " chars of reasoning"],
  [/^配置文件 · (.+)$/, (m, a) => "Config file · " + a],

  /* ── 自动更新(带版本号/时间/百分比) ── */
  [/^发现新版本 v(.+)，点击查看与安装$/, (m, a) => "New version v" + a + " available — click to view"],
  [/^发现新版本 v(.+)$/, (m, a) => "New version v" + a + " available"],
  [/^上次检查 (.+)$/, (m, a) => "Last checked " + a],
  [/^正在下载 (\d+)%$/, (m, a) => "Downloading " + a + "%"],
  [/^可用 · v(.+)$/, (m, a) => "Available · v" + a],
  [/^版本 v(.+) · Windows 单文件安装版$/, (m, a) => "v" + a + " · Windows single-file installer"],
  [/^发布于 (.+)$/, (m, a) => "Released " + a],
  [/^安装包 (.+) MB$/, (m, a) => "Package " + a + " MB"]
];

let LANG = "zh";

function _norm(s){ return String(s == null ? "" : s).replace(/\s+/g, " ").trim(); }

/* 单串翻译: 精确 → 规则 → 去掉前导图标(emoji/符号)再试 → 回落原文
   (宁可不翻, 也不产出空串; 图标前缀必须保留, 否则 🛡 安全运营 这类标签会漏翻) */
function tr(s){
  if(LANG !== "en" || !s) return s;
  const raw = String(s);
  const key = _norm(raw);
  if(!key) return raw;
  if(Object.prototype.hasOwnProperty.call(EN, key)) return EN[key];
  for(const [re, fn] of EN_RULES){
    if(re.test(key)){
      const out = key.replace(re, fn);
      if(out !== key) return out;
    }
  }
  const pre = raw.match(/^[^\u4e00-\u9fffA-Za-z0-9]+/);
  if(pre && pre[0].length < raw.length){
    const rest = raw.slice(pre[0].length);
    const out = tr(rest);
    if(out !== rest) return pre[0] + out;
  }
  return raw;
}

/* ---------- DOM 应用 ----------
   只处理界面容器; #chat 正文是模型输出/用户内容, 一律不碰。 */
const I18N_ROOTS = ["#sidebar", "#topbar", "#welcome", "#composer", "#overlay", "#overlay2",
                    "#toasts", "#jump-bottom"];
const I18N_SKIP_SEL = ".s-title, .sk-name, .sk-desc, textarea, code, pre, .md";
let _i18nBusy = false;
const _origText = new WeakMap();    // 文本节点 → 中文原文
const _origAttr = new WeakMap();    // 元素 → {属性: 中文原文}
const _origHtml = new WeakMap();    // 整块说明 → 中文 HTML 原文

function _skipNode(node){
  const p = node.parentElement;
  if(!p) return true;
  if(p.closest(I18N_SKIP_SEL)) return true;
  const tag = p.tagName;
  return tag === "SCRIPT" || tag === "STYLE" || tag === "TEXTAREA";
}

/* 整块 HTML 文案: 带 <b>/<small> 的说明段落按文本节点翻会被标签切碎成病句,
   由渲染方标 data-i18n-html="短标识"(见 EN_HTML), 这里整段替换。 */
const EN_HTML = {
  "connectors-intro":
    "Built-in capabilities and external access live here. <b>Unticking applies immediately</b> — the tool leaves the model's view, but the configuration is kept and can be re-enabled anytime.",
  "skills-intro":
    "A skill is a prompt fragment (markdown) injected into the system prompt when enabled. <b>The triage playbook is the “Security triage flow” skill</b> — edit it to change steps or output format, no code needed; edits are saved as a local copy that wins on upgrade.",
  "plugins-intro":
    "A plugin is a capability extension written by operators (a .py file): custom tools (TOOLS) plus usage notes (PLUGIN_PROMPT, injected into the system prompt). It applies <b>hot</b> after install and each plugin has its own switch. Interface: TOOLS=[{\"schema\": schema, \"handler\": fn, \"exec\": needs Execute mode}] + PLUGIN_PROMPT=\"when to use these tools\" + optional on_load(cfg). For prompt-only playbooks, the Skills section is simpler.",
  "customapi-intro":
    "<b>Connect any external system</b> — below: deep integrations (the types with a built-in parser; shown only when configured, removable and re-addable); then custom named access (any internal system, add what you need).",
  "mcp-intro":
    "MCP tools register as mcp__&lt;name&gt;__&lt;tool&gt;. <b>Changes apply only after “Save section”</b> (which reconnects). File access, web fetching, script execution and Office handling are already built in — no need to add MCP for those."
};

function applyI18nBlocks(root){
  root.querySelectorAll("[data-i18n-html]").forEach(el=>{
    const out = EN_HTML[el.getAttribute("data-i18n-html")];
    if(out && el.innerHTML.trim() !== out.trim()){
      if(!_origHtml.has(el)) _origHtml.set(el, el.innerHTML);
      el.innerHTML = out;
    }
  });
}

function applyI18n(root){
  if(LANG !== "en" || !root) return;
  if(_i18nBusy) return;
  _i18nBusy = true;
  try{
    applyI18nBlocks(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const hits = [];
    let n;
    while((n = walker.nextNode())){
      if(!n.nodeValue || !/[\u4e00-\u9fff]/.test(n.nodeValue)) continue;
      if(_skipNode(n)) continue;
      hits.push(n);
    }
    for(const node of hits){
      const raw = node.nodeValue;
      const trimmed = raw.trim();
      if(!trimmed) continue;
      const out = tr(trimmed);
      if(out === trimmed) continue;
      const lead = raw.slice(0, raw.indexOf(trimmed));
      const tail = raw.slice(raw.indexOf(trimmed) + trimmed.length);
      /* 记下原文: 切回中文时逐节点还原静态文案(重绘只能覆盖动态区) */
      if(!_origText.has(node)) _origText.set(node, raw);
      node.nodeValue = lead + out + tail;
    }
    root.querySelectorAll("[placeholder],[title],[aria-label]").forEach(el=>{
      ["placeholder", "title", "aria-label"].forEach(a=>{
        const v = el.getAttribute(a);
        if(!v || !/[\u4e00-\u9fff]/.test(v)) return;
        const out = tr(v);
        if(out !== v){
          const rec = _origAttr.get(el) || {};
          if(!(a in rec)){ rec[a] = v; _origAttr.set(el, rec); }
          el.setAttribute(a, out);
        }
      });
    });
  }finally{
    _i18nBusy = false;
  }
}

/* 还原中文: 把翻译过的文本节点/属性按原文写回(节点已重绘的自动跳过) */
function restoreI18nDom(){
  _i18nBusy = true;
  try{
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while((n = walker.nextNode())){
      const orig = _origText.get(n);
      if(orig !== undefined && n.nodeValue !== orig) n.nodeValue = orig;
    }
    document.querySelectorAll("[placeholder],[title],[aria-label]").forEach(el=>{
      const rec = _origAttr.get(el);
      if(!rec) return;
      Object.keys(rec).forEach(a=>{
        if(el.getAttribute(a) !== rec[a]) el.setAttribute(a, rec[a]);
      });
    });
    /* 整块 HTML 文案: 写回中文原文(设置中心重绘时也会重建) */
    document.querySelectorAll("[data-i18n-html]").forEach(el=>{
      const orig = _origHtml.get(el);
      if(orig !== undefined && el.innerHTML !== orig) el.innerHTML = orig;
    });
  }finally{
    _i18nBusy = false;
  }
}

function applyI18nAll(){
  I18N_ROOTS.forEach(sel=>{ const el = document.querySelector(sel); if(el) applyI18n(el); });
}

/* 动态渲染(设置分区/弹窗/提示/tool卡片)后自动补翻: 只观察界面容器, 聊天区不进观察范围 */
let _i18nTimer = null;
function scheduleI18n(){
  if(LANG !== "en") return;
  if(_i18nTimer) return;
  _i18nTimer = setTimeout(()=>{ _i18nTimer = null; applyI18nAll(); }, 24);
}

function installI18nObserver(){
  if(typeof MutationObserver !== "function") return;
  const obs = new MutationObserver(muts=>{
    for(const m of muts){
      if(m.type === "childList" || m.type === "characterData"){ scheduleI18n(); return; }
    }
  });
  I18N_ROOTS.forEach(sel=>{
    const el = document.querySelector(sel);
    if(el) obs.observe(el, {childList: true, subtree: true, characterData: true, attributes: true,
                            attributeFilter: ["placeholder", "title", "aria-label"]});
  });
}

/* 切换语言: 立即重绘界面文案; save=true 时同步落盘到 devices.json(引擎据此改回复语言) */
function setLang(lang, opts){
  const v = (String(lang || "").toLowerCase() === "en") ? "en" : "zh";
  const changed = v !== LANG;
  LANG = v;
  document.documentElement.lang = v === "en" ? "en" : "zh-CN";
  try{ localStorage.setItem(LANG_KEY, v); }catch(e){}
  if(v === "en"){
    applyI18nAll();
    /* 专家名/输入框提示/工具显示按钮由 JS 拼装(带 emoji), DOM 扫不到: 让状态刷新一次重绘 */
    try{
      if(window.__ezcodeBooted && typeof refreshState === "function") refreshState();
      if(typeof applyToolViewBtn === "function") applyToolViewBtn();
    }catch(e){}
  }else if(changed || (opts && opts.force)){
    /* 切回中文: 先把翻译过的静态文案逐节点还原, 再让动态区按中文原文重绘。
       设置中心重绘走内存编辑态(_setAll), 不会丢未保存的修改。 */
    try{ restoreI18nDom(); }catch(e){}
    try{
      if(document.querySelector("#overlay.show") && typeof renderSettings === "function" && _setAll){
        renderSettings();
      }
      if(window.__ezcodeBooted && typeof refreshState === "function") refreshState();
      if(typeof refreshSessions === "function") refreshSessions();
    }catch(e){}
  }
  if(opts && opts.save && window.pywebview && pywebview.api && pywebview.api.set_lang){
    try{ pywebview.api.set_lang(v).catch(()=>{}); }catch(e){}
  }
  return v;
}

function currentLang(){ return LANG; }

document.addEventListener("DOMContentLoaded", ()=>{ installI18nObserver(); });
