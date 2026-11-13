# 贡献指南

欢迎为 EZcode 提 Issue 或 Pull Request。这是一个本地优先的桌面 Agent 项目，改动请尽量保持「配置驱动、不绑定厂商」的设计原则。

## 开发准备

```powershell
git clone https://github.com/<你的用户名>/EZcode.git
cd EZcode
pip install -r requirements.txt
Copy-Item devices.default.json devices.json
python secops.pyw
```

调试时改用 `debug.bat`（保留控制台，可看 traceback）。前端改动后记得把 `index.html` 里的 `app.js?v=N` / `style.css?v=N` 版本号 +1，避免 WebView2 缓存。

## 几条约定

1. **不要把凭据写进代码或模板**。`devices.json`、`config.json`、`knowledge/criteria.json` 都被 `.gitignore` 挡住；新增配置项请同时更新 `devices.default.json`（无凭据）与 `devices.example.json`（带说明）。提交前跑 `python build/check_public.py`。
2. **新接入不要点名厂商**。UI 文案、提示词、示例配置里用「告警检索系统 / 主机侧告警系统 / 资产库」这类通用词；内部配置节名（`ngsoc` / `edr` / `netbox`）属于兼容标识，保持不变即可。
3. **工具按配置注册**。无配置节时工具不应出现在模型的工具列表里（`tools/__init__.py` 的 `configure`）。新工具请在 `caps.py` 打能力标签，验证门会按标签审计。
4. **写操作要有确认门**。任何修改文件、执行命令、回写处置的动作都要走权限确认，不能由子代理绕过。
5. **保持前端可测**。UI 改动请跑：
   ```powershell
   node build/test_renderer.js              # Markdown 渲染回归
   python build/diag_settings.py            # 设置中心+专家中心+连接器+MCP+文档处理+页面布局+输入框工具栏+文件选择框兼容+品牌落地+中英文切换+文档读取+工具结果脱敏+后台任务事件缓存+厂商快捷接入+完成提示 222 项真实窗口验证
   python build/diag_update.py              # 自动更新 80 项(多源并行取最高版本/失败降级/GitHub 与静态清单/灰度与忽略/sha256 与篡改拦截/发布清单生成/前后端接线)
   python build/diag_providers.py           # 模型厂商接入 43 项(预设完整性/profile 生成/落盘字段归一/连接测试边界/桥接与前端接线)
   python build/diag_notify.py              # 完成提示 44 项(文案清洗/触发策略/偏好解析/回调透传/真实透明窗: 尺寸·位置·置顶·不抢焦点·渲染·点击关闭)
   python build/diag_scroll.py              # 滚动跟随 26 项验证
   python build/diag_scroll_real.py         # 真实切窗场景 8 项(失焦→后台推流→切回)
   python build/diag_bigdoc.py              # 大文档事故复现 14 项(防空转/分页/章节索引/文档内检索闭环)
   python build/diag_sessions.py            # 多任务并行 39 项(并发新建/专家模型隔离/切走后后台继续跑完/会话级授权隔离)
   python evals/run_evals.py                # 确定性评测集 65 项
   python build/test_sandbox.py             # 沙箱冒烟
   ```
   诊断脚本会拉起真实 pywebview 窗口，运行时请勿手动关闭。

## 提 PR

- 一个 PR 聚焦一件事，说明「改了什么 / 为什么 / 怎么验证」
- 附自检与回归结果；涉及 UI 的可附截图
- 不要提交 `*.exe`、`*.zip`、`build/_*`、`logs/` 等构建产物或运行数据

## 许可证

贡献即表示同意以 [MIT](LICENSE) 许可发布。
