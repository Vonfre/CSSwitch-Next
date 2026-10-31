# CSSwitch Next Desktop

Tauri 2 + 原生 HTML/CSS/JavaScript。运行方式、依赖、协议范围与验证说明见 [项目 README](../README.md)。

```bash
npm ci
npm run prepare:assets
npm run dev
npm run build
npm run test:ui
```

`src-tauri/src/config.rs` 管理原子配置与密钥掩码；`lib.rs` 管理连接命令及共享 Science 生命周期；`science_shared.rs` 使用原 HOME/data-dir 和独立 auth-dir，保留历史归属并阻止免登录进程读写认证钥匙串；`oauth_forge.rs` 只写 CSSwitch 的本地认证；`proc.rs` 提供进程定位与回环探活。

代理模块在 `../proxy/`。打包时代理、启动脚本、许可证一起进入 App Resources。网关在每个请求开始时读取配置快照，因此配置更新无需重启网关。连接验证使用独立短进程，不改变活动连接。

`CSSWITCH_DATA_DIR` 可将测试数据指向临时目录；`CSSWITCH_REPO` 仅用于显式指定开发资源目录。正式 App 使用自身 Resources 中的脚本。

“MCP 管理”接入 Science 安装资源中的 24 个本机科研连接器，也可添加本地 stdio / 远程 HTTPS MCP，管理绑定、授权和工具权限。Ketcher 需要 Node.js 22+，其他科研连接器使用 Science 自带 Python，管理桥接需要系统 Python 3。0.8.0 起不再打包或默认安装 OpenScience 聚合服务。使用及验证说明见 [MCP 管理](../docs/MCP.md)。

0.8.1 的设置页增加本机兼容性检查：区分实际 CLI 版本与应用包版本，检查完整运行资源、启动参数和连接器依赖。`science_compat.rs` 调用只读脚本 `science-compatibility.py`；版本及帮助探针各设 5 秒超时，检查不启动 daemon、不请求模型、不读取密钥。导出报告通过字段白名单排除本机路径。启动阶段通过 Tauri 事件显示；已运行的 Science 使用 `open_url` 直接打开，不重复接入 MCP。新启动为 Science 的 HTML 预览服务自动分配空闲端口。

提供商配置将连接、模型映射和调用测试集中在一个表单中；宽窗口并排显示连接与模型，窄窗口纵向排列，底部始终保留保存操作。运行卡片使用“停止服务”文字按钮。

设置中的“应用与环境”分别显示实际启动、应用包和运行资源版本。进入设置及返回设置窗口时会重新检测本机信息；兼容性由启动参数、资源版本匹配和依赖检查判断，不再用固定版本号作为通过条件。应用包版本可以落后于自动更新后的启动版本，本机检查通过不代表全部页面或远程服务已验证。

## 应用更新

0.5.0 起可在「设置 → 应用更新」检查、下载及安装新版；启动时也会自动检查。下载包通过签名校验后才能安装，安装前请结束任务并停止本地网关。详见 [应用内更新](../docs/UPDATES.md)。

## 管理工作空间

0.8.3 的通用界面样式位于 `src/workspace.css`，`src/ui.js` 提供上下文帮助、主题与界面字体设置，`src/settings.js` 管理分组设置页。本机 MCP 开关由 `src/mcp.js` 管理，保留与汉化设置相互独立的配置字段。辅助说明可按需查看，异常状态始终直接显示。

新增的 Skills、用量和会话页面分别在 `src/skills.js`、`src/usage.js`、`src/sessions.js`。`workspace.rs` / `scripts/science-workspace.py` 处理只读历史、本地技能 / 仓库发现及 CSSwitch 设置；`mcp.rs` / `scripts/science-mcp-manage.py` 处理 Science 拥有的 MCP 和 Skills 操作。完整功能边界及测试命令见 [管理工作空间](../docs/MANAGEMENT.md)。
