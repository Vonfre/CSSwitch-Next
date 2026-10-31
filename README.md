# CSSwitch Next

一个用于 Claude Science 的 macOS / Windows 第三方 API 管理与切换工具。

- 自行添加 API 提供商，管理连接和密钥。
- 自动获取 API 可选模型，配置 Sonnet（默认）和 Opus（质量）映射，分别选择协议并测试连接。
- 自动读取本机 Science 的原生模型目录，聊天框及默认、子代理、审阅选择器显示「原模型 → 映射模型」。
- 在官方模式与第三方 API 之间切换，沿用本机对话记录。
- 支持 Anthropic Messages、OpenAI Chat Completions 和 OpenAI Responses。
- 查看用量趋势、模型统计、缓存用量和预估费用，筛选与导出请求记录。
- 按项目或时间浏览 Science 会话，进入全窗口阅读页查看提问、回复和执行过程，搜索内容、定位消息、导出完整记录。
- 从本地及 GitHub 发现 Skills，批量安装、启停和卸载，多文件 / 多技能 ZIP 导入，检查并更新已管理的技能。
- 查看运行日志。
- 分组设置页支持跟随系统的深浅外观，以及系统默认、苹方、Helvetica、宋体四种界面字体。
- 在设置中检查 Science 的实际启动版本、应用包版本、运行资源和连接器依赖，导出不含密钥及本机路径的检查报告。
- 启动时显示真实阶段及等待时间；再次打开已运行的 Science 直接复用服务。
- 管理本地与远程 MCP，支持预设、筛选和标准 JSON 导入，接入 Science 的 24 个本机科研连接器。
- 第三方模式使用本机 MCP，PubMed、ChEMBL、bioRxiv、Clinical Trials 使用本地版本，科研工具调用无需 Claude 托管 MCP 登录。
- Science 页面汉化支持本地词库、Edge 自动翻译和 AI 自动翻译；动态发现新界面文案并缓存，保留对话正文。
- 在应用内检查、下载并安装更新。

[下载 macOS / Windows 安装包](https://github.com/Vonfre/CSSwitch-Next/releases/latest)

[Windows 安装与构建](docs/WINDOWS.md) · [管理工作空间使用说明](docs/MANAGEMENT.md) · [汉化与翻译方式](docs/TRANSLATION.md)

[本机优化与后续规划（Science 0.1.67）](docs/ROADMAP-0.9.md)
