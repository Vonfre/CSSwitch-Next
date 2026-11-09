# 跨平台功能与发布

0.8.21 将 macOS 的四档模型配置与 Windows 0.8.20 的路径、订阅更新合并到同一版本，后续从同一提交构建三种平台产物。

| 功能 | macOS Apple Silicon / Intel | Windows x64 |
| --- | --- | --- |
| 第三方模型 | Fable、Opus、Sonnet、Haiku 四档独立模型与协议 | 相同配置、界面和路由 |
| Science 模型菜单 | 对话、默认、子代理和审阅均显示「档位 → API 模型」 | 相同菜单适配；兼容旧版模型目录 |
| 官方订阅 | 官方 OAuth、加密存储、原生登录切换与回滚 | 相同功能，使用 DPAPI 和 NTFS 私有权限 |
| 会话与 Resume | 切换前停止 Science，使用 APFS 快照保留会话；手动 Resume | 同磁盘暂存和独立复制，保留数据库、工具结果与 Resume 状态 |
| Science 目录 | 沿用本机数据目录；订阅切换限默认布局 | 共用 AppData / 自定义目录识别，支持中文、空格和环境变量；多个历史目录需明确选择 |
| MCP、Skills、会话、用量、汉化 | 共用管理界面与代理模块 | 共用管理界面与代理模块，保留 Windows 路径及进程适配 |
| 安装与更新 | arm64 / x64 DMG、APP ZIP、签名更新包 | x64 NSIS 安装包、含 Python / Node.js 的便携 ZIP、签名更新包 |

## 模型配置兼容

在提供商配置中一次填写四档，每档可选择 Anthropic Messages、OpenAI Chat Completions 或 OpenAI Responses。API 地址与密钥由同一连接共用。旧的默认、质量及按版本映射会自动预填，打开配置本身不会改写保存值；保存后统一使用档位映射。Sonnet 必填，其他档位模型留空时使用 Sonnet 的模型 ID，协议仍按各档设置。

Science 第三方菜单仅列四档，例如 `Fable → gpt-6-astra`。旧 Science 没有 Fable 时使用内部兼容标识，代理仍按 Fable 的模型和协议发送请求。旧会话的版本选择归并到对应档位，不改写对话内容。官方订阅目录、登录错误与组织策略继续由 Science 管理。

## 验证与发布

GitHub Actions 在同一提交上执行 Python、Node、Shell、浏览器和 Rust 检查；Windows 额外使用 Edge 验证模型配置、订阅页面和旧模型选择迁移，并检查内置运行库。通过后构建 macOS 双架构与 Windows x64，验证更新签名和 SHA-256，再统一发布 Release 与 `latest.json`。

本机 macOS 使用隔离的 Science 实例和回环上游验证四档真实对话请求的模型与协议，不向用户的 API 发送付费测试。Windows 自动测试覆盖订阅凭据、SQLite 会话迁移及回滚；尚未完成两个真实官方账号的授权切换实测。具体版本、路径限制及 Resume 边界见[订阅文档](SUBSCRIPTIONS.md)和 [Windows 指南](WINDOWS.md)。
