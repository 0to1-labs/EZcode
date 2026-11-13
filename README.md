<p align="center">
  <img src="assets/EZcode-icon.png" width="128" alt="EZcode 松鼠图标">
</p>

<h1 align="center">EZcode</h1>

<p align="center">
  <b>本地优先的 Windows 桌面 AI 应用 —— 安全运营、通用编码、日常工作助手，一位专家一个场景。</b><br>
  <i>Local-first Windows desktop AI workbench: bring your own LLM, name your own integrations, keep your data on your machine.</i>
</p>

---

EZcode 是一个跑在 Windows 桌面上的 AI 应用（pywebview 原生窗口 + Python 引擎），内置三位专家：**安全运营**（告警研判/事件调查）、**通用编码**（写代码/改文件/跑命令）、**日常工作助手**（写作/总结/翻译/查资料/待办管理）。它不绑定任何厂商：所有外部系统都由你在设置中心**自己命名接入**，没配置的接入连工具都不会注册到模型视野里。内置工具箱（文件与代码、联网查询、分析脚本、本地待办清单）默认全部开启，不在界面暴露。

## ✨ 特性

| 能力 | 说明 |
|---|---|
| 🏷 **不绑定厂商** | 系统 API 接入由你命名（名称 / 地址 / 密钥 / 能力标签），研判提示词按「能力」动态注入，不点名任何具体产品 |
| 🔌 **配置驱动注册** | 无配置节 → 工具不注册 → 模型看不到也调不了；「移除」是真删配置节，「添加」可加回 |
| 🧠 **专家中心** | 任务 × 专家：每位专家决定任务的系统提示词与工具面。内置「安全运营 / 通用编码 / 日常工作助手」，可在界面新建自建专家（`agents/personas/*.md`，保存即生效、可删除）；**上次用的专家会记住**，重开客户端直接回到那位专家（`ui.persona`，专家被删则回落安全运营） |
| 🧾 **工具调用不刷屏** | 三档显示：紧凑一行 / 详细卡片 / 完全隐藏，运行时顶部仍有进度提示；**只展示调用概要（工具名 + 参数键名 + 状态耗时），不展示返回的原始 JSON**，`api_key`/`secret` 等敏感值一律脱敏，完整结果只写进会话日志 |
| 🔒 **逐次权限确认** | 写文件、执行命令（含 shell / Python）、处置回写均需弹窗确认，无"免确认"捷径；120 秒无响应自动拒绝；破坏性命令默认 deny |
| 🧊 **Windows 沙箱** | 内置 Python 分析脚本默认跑在低完整性令牌 + Job Object 中（内存上限、超时强杀、写不进沙箱外） |
| ⇲ **上下文压缩** | 水位 ≥80% 自动生成历史纪要并保留最近 8 条原文；也可手动点「压缩」。会话文件始终全量留存 |
| 📄 **读文档** | Word/Excel/PPT/PDF 用 `doc_read` 直接取文字（按段落/工作表/页组织）；docx/xlsx/pptx 是压缩包，`file_read` 只会读到乱码——这是"读不出 Word 内容"的根因。大文档支持 `outline` 章节索引、`offset` 翻页与 `find` 关键词定位，结果带 `next_offset` 与明确 hint，不会被 24000 字硬截断后无路可走 |
| 🔎 **opencode 式检索内核** | `fs_grep` 可直接检索 Word/Excel/PPT/PDF **内部文字**（自动解包，不再当二进制跳过），命中返回 `pos` 字符偏移，可直接填进 `doc_read(offset=pos)` 精读；支持 `context` 上下文行。大文档三步走：章节索引 → 关键词定位 → 定点精读。可执行模式下 `shell` / `run_python` 可自由执行，**每次执行前都弹窗人工确认** |
| 🗂 **多任务并行且互不影响** | 任务列表里每个任务有独立的对话上下文、专家、模型、执行模式与权限授权；A 还在跑时可直接新建/切到 B 继续干活，**A 在后台完整跑完**，事件按 `sid` 缓存、切回时补出期间的正文与思考（不串台、不空白、不锁在"运行中"）。后台任务的权限确认会**立即弹窗并标注来源任务**（不再干等超时），跑完的任务在列表里显示「有新回答」。运行中任务锁定专家，新任务可自由切专家/模型，列表中可直接停止后台任务 |
| ⏱ **防空转** | 单轮有时间预算（到点强制基于已有信息收尾给答案）、重复调用拦截（同一段反复加大 `max_chars` 重读直接拒绝）、能力清单按当前模式实时生成（只读模式不再宣称 `shell`/`run_python` 可用）；"信息够了就立刻作答"写进系统提示词 |
| 🛠 **Skills** | 全局 `~/.ezcode/skills/*.md` + 项目级 `skills/*.md`，同名按项目级覆盖，设置中心可增删改与启停；技能不区分来源、不打「内置」标签 |
| 🧷 **逐条独立研判** | 研判技能内置铁律：每次研判只认本轮取证，历史结论只能是「待核查线索」，不能当定性依据（旧结论也可能是错的） |
| 🔔 **完成提示** | 回答结束时在屏幕右下角弹一张圆角浮窗（专家图标 + 任务名 + 摘要 + 用时/工具数 + 倒计时条），不抢焦点；点卡片直接回到那个任务。默认「自动」——只在你看别的任务或窗口不在前台时才提醒；设置中心可改「总是 / 关闭」 |
| 🌐 **中英文双语** | 设置中心 › 运行偏好 › 界面语言 可切 中文 / English，界面文案、模型回复语言与本地配置一起切换（`ui.lang`） |
| 🧩 **插件** | 运营人员把研判思路写成 `.py` 插件放进 `plugins/`，提供工具 + 附加提示词，带独立开关 |
| 🌗 **体验** | 流式输出、Markdown 表格/代码块渲染、明暗主题、会话管理、模型热切换、导出会话 |

## 🚀 安装（推荐：单文件安装包）

从 [Releases](../../releases) 下载 **`EZcodeSetup.exe`**，双击即可。

- 单文件、无需管理员权限、无需预装 Python、无需浏览器
- 自动创建开始菜单 / 桌面快捷方式与「应用和功能」卸载入口
- 安装到 `%LOCALAPPDATA%\Programs\EZcode`，全部数据留在本机
- **首次启动**：右上角 `⚙ 设置` → `🧠 大模型` → 直接点一个厂商（DeepSeek / 通义千问 / Kimi / 智谱 / 豆包 / 混元 / 硅基流动 / 千帆 / OpenAI / Claude），**只填 API Key** 点「接入并使用」即可（可先点「测试连接」验证）。模型名可从预设里选，也能手动改；需要自建 vLLM / Ollama 或其它 OpenAI 兼容服务时用下方「自定义配置」手填 `base_url` / `model`，两种方式并存
- **升级 / 重装不丢配置**：安装器会保留已有的 `devices.json`、`config.json`、`knowledge/criteria.json`（含你填过的密钥、接入系统与判据库）；首次安装才释放无凭据默认值

> 发布包内**不含任何凭据**：内置的是无密钥的 `devices.default.json`，构建时有发布门硬校验，混入密钥 / 本机路径 / 内网地址会直接终止构建。
>
> 本机从旧版本迁移、或重装后想恢复私有配置时，运行 `python build/restore_local_config.py`：它会把源码目录的私有配置、判据库、插件、历史会话与技能恢复到已安装版本，覆盖前自动备份，且不会打印任何密钥。

## 🛠 从源码运行

环境：Windows 10/11、Python 3.11、[WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)（Win11 通常已自带）。

```powershell
git clone https://github.com/0to1-labs/EZcode.git
cd EZcode
pip install -r requirements.txt
Copy-Item devices.default.json devices.json      # 首次运行配置（含凭据，已被 .gitignore 挡住）
python secops.pyw                                # 或双击 .\start.bat
```

命令行形态（可选）：`python tui.py` 全屏 TUI、`python agent.py` 单轮 CLI、`python gui.py` 早期 WebView 版。

## 🧭 页面布局

参照「新建任务 → 选专家 → 配能力 → 下任务」这条主线组织，侧栏从上到下分五层：

| 区域 | 内容 |
|---|---|
| **品牌区** | 松鼠 logo + `EZcode` + 版本号 |
| **主操作** | `＋ 新建任务`（清空当前会话，恢复自动跟随） |
| **任务列表** | 历史任务（带条数与时间），可切换 / 删除 |
| **工作区** | 专家中心、技能、连接器 —— 每个入口显示标题 + 一句话说明 |
| **设置中心** | 底部独立入口 |

「专家中心」下的「当前任务专家」默认收起（点右侧小箭头展开），当前专家在顶栏以徽标显示；原先左下角那组「运行状态」胶囊已全部移进输入框工具栏，侧栏不再堆控件。

**输入框工具栏**（对齐 WorkBuddy 的底部排版，从左到右）：

| 位置 | 控件 | 作用 |
|---|---|---|
| 左 | `＋` | 附加文件证据（选本地文件写入 `@路径`）、选择专家、工具显示档位、明暗主题、设置中心 |
| 左 | 权限选择 | `只读查询 / 可执行`，下拉切换，运行中禁止切换 |
| 右 | 上下文百分比 | 环形水位 + 百分比，点击展开「立即压缩上下文」；超过 80% 变警示色 |
| 右 | 模型名称 | 下拉列出已配置 profile，选中即热切换，附「管理模型…」入口 |
| 右 | `↑` | 发送；运行中变 `■` 停止 |

主区顶栏只保留任务标题、当前专家徽标与运行状态点；新任务无历史消息时展示欢迎页与 4 张能力卡片（点击即把示例任务填入输入框）。

## ⚙ 设置中心（4 组 · 10 个分区）

左侧导航按用途分组，每个分区带一句话说明（不显示配置状态徽标，避免与服务端状态不同步）。

| 分组 | 分区 | 用途 |
|---|---|---|
| **模型与运行** | 🧠 大模型 | 10 家厂商**快捷接入（只填 Key，可测连通性）**；自定义 profile 仍可手填地址与模型，`active` 指定默认、运行中可热切换 |
| | 🎛 运行偏好 | **界面语言（中文 / English）**、明暗主题、工具调用显示档位、**完成提示（右下角浮窗：自动 / 总是 / 关闭）**、只读/可执行模式、手动压缩上下文 |
| **能力接入** | 🔌 系统API接入 | 自定义命名接入（地址 / 密钥 / 能力标签）+ 深度接入（代码有专用解析器的几类，配置全由你填写）；需要什么系统自己加 |
| | 🔗 MCP服务器 | 7 个内置模板（context7 / memory / sqlite / postgres / github / playwright / sequential-thinking）勾选即启用；也支持自定义 stdio / Streamable HTTP。列表只显示名称与启用状态，详情折叠可编辑 |
| | ⚙️ 内置工具箱 | 文件与代码 / 联网查询 / 分析脚本 / 本地待办清单，**默认全部开启、不在界面暴露**；需要临时关闭某组时在 `devices.json` 里配 `builtin_tools` |
| **扩展与内容** | 🛠 技能 | 技能清单（研判流程等随包提供，点「编辑」自动另存为本机副本覆盖）与自建 Markdown 技能的编辑与开关；不打来源标签 |
| | 🧩 插件 | 安装 / 启停本地 `.py` 插件，查看插件注入的思路说明 |
| | 📚 RAG知识库 | 外部检索服务（地址 / 密钥 / 请求字段 / 超时） |
| **关于** | ⬆ 版本更新 | 启动自动检查更新、多更新源配置、下载与一键升级（详见「自动更新」章节） |
| | 🐿 关于 EZcode | 版本、MIT 协议、本机数据位置与 SmartScreen 说明 |

> 侧栏「技能」「连接器」是快捷入口，分别直达设置中心的对应分区，不重复造编辑器。
> 完整配置项见 [`devices.example.json`](devices.example.json)。

## 🧱 多设备：统一能力层与自动适配

设备差异被适配器屏蔽，模型只面向三个统一工具，研判方法论与提示词不随设备变化：

| 工具 | 作用 |
|---|---|
| `sec_probe` | 设备自检：逐台校验 token 权限、可达性、能拉到多少条告警与哪些字段 |
| `sec_search` | 项目内并发聚合检索，字段归一化；**同一条告警被多台设备命中会合并并在 `sources` 标注来源** |
| `sec_detail` | 按告警 ID 路由到来源设备下钻报文/证据 |

**项目 = 设备组**：一个项目下挂多台设备（流量侧站点、主机侧项目、WAF/防火墙等 HTTP 接口）。`sec_search(project=...)` 一次并发查全组，按指纹（源目 IP + 目端口 + 规则名 + 5 分钟时间桶）聚类，天然形成「一条告警 + 多方证据」的组合研判素材。旧配置（站点 / 项目 / 自定义接入）自动归入「默认项目」，不改盘。

**自动适配（声明式，不执行代码）**：HTTP 类设备首次接入时，**先把该设备的接口文档粘贴到「文档」里**（告警列表接口的请求参数、响应字段、鉴权方式），再点「自动适配」→ EZcode 取样（字符串值脱敏）→ 当前模型结合**文档 + 真实样本**生成字段映射 → **用真实响应跑一遍校验**（产不出合法告警就把错误回给模型重试，最多 2 次）→ 预览确认后保存生效。文档告诉模型字段含义、样本告诉它实际结构，比只给样本准得多；文档与样本冲突时以样本为准。映射是纯数据，可手改、可回滚；模型不会生成也不会执行任何代码。

**接入一台新设备**：内置类型（`ngsoc` / `edr`）直接加设备；其他设备用 `kind: "http"` + 自动适配生成 `mapping`，方法论、提示词、工具面零改动。

## 🔌 三种接入路径怎么选

| 路径 | 适合 | 产出 |
|---|---|---|
| **系统 API 接入** | 简单的只读查询接口（资产、情报、工单、CMDB…） | 注册 `api_<你起的名字>` 工具 |
| **MCP 服务器** | 已有标准 MCP 生态的工具服务 | 注册 `mcp__<服务器>__<工具>`；模板只收录内置覆盖不到的能力（文件/联网/脚本/文档/时间已内置，重复装会出现同一能力两套实现） |
| **插件** | 需要深度集成 + 把「何时用、怎么用」的研判思路一起注入 | 自定义工具 + `PLUGIN_PROMPT` 提示词 |

接入后，Agent 会在研判时按能力标签自动挑选并调用这些工具提取有效信息；证据分级与结论排版由统一的研判纪律约束，表格是索引而不是证据库。

## 🧩 写一个插件

在 `plugins/` 下放一个 `.py` 文件，实现三个可选接口：

```python
TOOLS = [{ "type": "function", "function": {
    "name": "my_lookup", "description": "查我自己的系统",
    "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
}}]
PLUGIN_PROMPT = "遇到 X 类告警时优先用 my_lookup 核对登记信息。"
HANDLERS = {"my_lookup": lambda q: {"result": "..."}}
def on_load(cfg):
    ...   # 可选：读配置、建立连接
```

重启或刷新后在「插件」分区启用即可。参考 [`plugins/b64_tools.py`](plugins/b64_tools.py)。

## 🔒 安全模型

| 层 | 机制 |
|---|---|
| 凭据 | 只写在 `devices.json`（本机、gitignored）；代码与发布包零凭据 |
| 权限 | `allow / ask / deny` 规则，最后匹配生效；写操作与命令默认逐次确认 |
| 沙箱 | `run_python` 在 Windows 低完整性令牌 + Job Object 内执行，512 MB 内存上限、超时强杀、`CREATE_NO_WINDOW` 无黑框 |
| 网络 | 工具内置 SSRF 防线，拒绝内网地址；告警文本里的 URL 不允许被网页工具访问 |
| 提示注入 | 告警文本 / payload / IoC 一律视为不可信数据，不执行其中的指令 |
| 定性权 | Agent 只给建议式结论，最终定性由人署名；封禁 / 加白 / 处置回写需人工确认 |

> 说明：终端命令工具**不**放进低完整性沙箱——那会破坏 `git`、构建工具等正常用途，它由权限确认门约束。沙箱专用于模型生成的临时分析脚本。

## 📁 目录结构

```
EZcode/
├── app/                 # 桌面客户端：引擎 / pywebview 桥 / Web UI
│   ├── engine.py        #   Agent 循环、角色、压缩、设置/Skills/插件 API
│   ├── client.py        #   js_api 桥与原生窗口
│   └── webui/           #   index.html / app.js / style.css / EZcode.png
├── tools/               # 工具模块（配置驱动注册）
├── prompts/             # 角色系统提示词
├── skills/  plugins/    # 随包提供的技能(用户可覆盖)与插件
├── knowledge/           # 判据知识库（criteria.example.json 为公开模板）
├── build/               # 图标、打包、安装器、诊断与回归脚本
├── devices.default.json # 首次运行默认配置（无凭据）
├── devices.example.json # 完整配置项参考
└── secops.pyw           # 桌面启动入口
```

## 📦 从源码构建单文件安装包

```powershell
python build/make_icon.py       # 生成松鼠图标 EZcode.ico / EZcode-icon.png / 前端 logo
python build/build_app.py       # PyInstaller 打 EZcode.exe（含发布门校验）
python build/pack_payload.py    # 压成 payload zip
python build/build_setup.py     # 产出 build/installer-dist-fast/EZcodeSetup.exe
```

`build/build_app.py --check-only` 可单独跑发布门（不打包）。回归脚本：`build/diag_settings.py`（设置中心+专家中心+连接器+MCP+文档处理+页面布局+输入框工具栏+文件选择框兼容+品牌落地+中英文切换+文档读取+工具结果脱敏+后台任务事件缓存 210 项真实窗口验证）、`build/diag_update.py`（自动更新 80 项：多源并行取最高版本 / 单源失败降级 / GitHub 与静态清单解析 / 灰度与忽略 / sha256 校验与篡改拦截 / 发布清单生成 / 前后端接线）、`build/diag_bigdoc.py`（大文档事故复现 14 项：重复重读拦截 / 时间预算收尾 / 分页与关键词定位 / 章节索引 / fs_grep 文档内检索→精读闭环）、`build/diag_sessions.py`（多任务并行 39 项：并发新建 / 专家模型模式隔离 / 事件 sid 不串台 / **运行中切换后后台继续跑完** / 未读标记 / 会话级授权隔离 / 切回与持久化 / 中止删除隔离）、`build/diag_scroll.py`（滚动跟随 26 项）+ `build/diag_scroll_real.py`（真实切窗 8 项）、`node build/test_renderer.js`（渲染 27 项）、`python evals/run_evals.py`（评测 65 项）、`build/test_sandbox.py`、发布前 `build/check_public.py`。

## 🔄 自动更新：如何把新版本推给已安装的客户端

**没有服务端，也不需要服务端主动推送。** 客户端启动时自己拉一份静态清单 `version.json`，发现清单版本比本机高，就在侧栏版本号旁显示 `⬆ v1.0.49` 徽标，点开「设置中心 → 版本更新」查看说明、下载、安装。整个链路是「客户端拉取 + 用户确认」：不驻留后台服务、不建长连接、不上传任何本机数据。

### 一、发布一个新版（3 步）

1. **改版本号**：只改根目录 `ezcode_version.py` 的 `APP_VERSION` / `APP_BUILD`。构建脚本会自动同步 exe 版本资源、注册表版本与界面版本，不用再改第二处。
2. **出包 + 生成清单**：

```powershell
python build/build_app.py
python build/pack_payload.py
python build/build_setup.py
copy build\installer-dist-fast\EZcodeSetup.exe EZcodeSetup.exe
python build/make_release.py --repo yourname/EZcode --notes "修复多任务切换后后台思考中断"
```

产出 `dist/update/EZcodeSetup-<版本>.exe` 与 `dist/update/version.json`（含 sha256、包大小、中英文更新说明、最低版本 / 灰度 / 强制升级字段）。

3. **推到更新源**（下面二选一，或都做）。

### 二、两种更新源，可以同时启用

| 源 | 清单从哪来 | 客户端怎么配 |
|---|---|---|
| GitHub Releases | `GET https://api.github.com/repos/<owner>/<repo>/releases/latest`，读 `tag_name` 和 `.exe` 资产；Release 里若附了 `version.json`，取其中的 sha256 与更新说明 | 设置中心「版本更新」填 `owner/repo`；或写入 `ezcode_version.py` 的 `GITHUB_REPO` 后重新出包 |
| 国内服务器 / 内网 | 静态目录放 `version.json` + 安装包，支持 HTTPS、UNC 共享路径、本地文件 | 启用「国内更新服务器」，填 `https://your.domain/ezcode/version.json` |

GitHub 推送：

```powershell
gh release create v1.0.49 dist/update/EZcodeSetup-1.0.49.exe dist/update/version.json --title "EZcode v1.0.49" --notes "修复…"
```

也可以在 GitHub 网页新建 Release：tag 用 `v1.0.49`，把这两个文件拖进去即可。私有仓库或需要更高 API 限额时，在 `devices.json` 的 `update.sources[0].token` 填 PAT；国内下载慢可填 `mirror`，把包地址挂到镜像前缀。

国内服务器推送：

```powershell
python build/make_release.py --base-url https://your.domain/ezcode --notes "修复…"
# 把 dist/update/ 下两个文件同步到该目录即可
```

Nginx / 对象存储 / 内网 IIS / 共享盘都行，只要能 GET 到 `version.json`。记得给清单加 `Cache-Control: no-cache`，否则旧清单被缓存会让客户端看不到新版本。

**两条源都启用时**：客户端启动后台**并行**检查全部源，任一可达即可，取版本号最高的那份。GitHub 不可达或限频时国内源照常生效，反之亦然。以后加第三条源也只是在 `devices.json` 的 `update.sources` 数组里加一项，不需要改代码。

### 三、客户端侧发生什么

1. 启动后台线程检查（默认 6 秒超时；失败静默，不弹框、不拖慢首屏）
2. 有新版本 → 侧栏出现 `⬆ v1.0.49`，设置中心显示版本说明与每条源的状态
3. 点「下载更新」→ 流式下载到 `%LOCALAPPDATA%\EZcode\updates\`，比对 sha256 与大小；校验失败直接删掉，绝不交给安装器
4. 点「安装并重启」（需二次确认）→ 客户端关闭 → `EZcodeSetup.exe --silent --launch` 延迟启动，覆盖安装后自动重启；`devices.json`、`criteria.json`、会话、技能、插件全部保留
5. 不想升可以点「忽略此版本」，或关掉「启动时自动检查」

### 四、灰度、强制升级与回滚

`version.json` 里 `rollout`（0-100）按机器标识稳定取模做灰度；`min_version` / `force` 用于必须升级的版本（会覆盖「忽略此版本」）。**回滚不要调低版本号**（客户端只认更高的版本），正确做法是发布一个更高版本号并把 `package.url` 指向上一版安装包。

### 五、安全边界

只出站 GET 两样东西：版本清单、安装包。不上传配置、会话、告警或凭据。清单走 HTTPS，安装包强制 sha256（GitHub Release 未附 `version.json` 时会跳过校验并在界面标注）。`devices.json` 含凭据且被安装器保留，绝不会被更新包覆盖。

## 🤝 贡献与许可

- 欢迎提 Issue / PR，见 [CONTRIBUTING.md](CONTRIBUTING.md)；安全私密披露见 [SECURITY.md](SECURITY.md)
- 提交前请跑 `python build/check_public.py`，确认没有把 `devices.json`、密钥或内部案例带进仓库
- 许可： [MIT](LICENSE)

## ⚠️ 免责声明

本项目只做辅助研判与建议，不替代人工判断。任何封禁、加白、处置回写等动作请由具备权限的人员在对应系统上手动执行；使用第三方 LLM / API 时请自行评估数据合规要求。
