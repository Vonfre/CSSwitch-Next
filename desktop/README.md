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

“MCP 管理”可安装 43 个科研数据库适配器，也可添加本地 stdio / 远程 HTTPS MCP，管理绑定、授权和工具权限。科研服务需要 Node.js 22+，管理桥接需要 Python 3；Tauri dev/build 会自动构建科研服务并打包所需许可证。使用及验证说明见 [MCP 管理](../docs/MCP.md)。

## 应用更新

0.5.0 起可在「设置 → 应用更新」检查、下载及安装新版；启动时也会自动检查。下载包通过签名校验后才能安装，安装前请结束任务并停止本地网关。详见 [应用内更新](../docs/UPDATES.md)。

## 管理工作空间

新增的 Skills、用量和会话页面分别在 `src/skills.js`、`src/usage.js`、`src/sessions.js`。`workspace.rs` / `scripts/science-workspace.py` 处理只读历史、本地技能 / 仓库发现及 CSSwitch 设置；`mcp.rs` / `scripts/science-mcp-manage.py` 处理 Science 拥有的 MCP 和 Skills 操作。完整功能边界及测试命令见 [管理工作空间](../docs/MANAGEMENT.md)。
