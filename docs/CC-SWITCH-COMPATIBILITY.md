# CC Switch 与 CSSwitch Next 的兼容方式

本次核对 CC Switch 源码版本 4.0.28，commit cdca0984231021c4eb7e8978af8670cd29c6e415。

## 运行依赖

CC Switch 的核心后端是 Rust/Tauri，没有在应用资源配置中打包 Python 运行时。Cargo.toml 使用 reqwest/rustls、rusqlite（bundled）等原生库；tauri.conf.json 声明 macOS 最低 12.0。它按系统和 CPU 架构发布产物，而非要求所有设备拥有同一套外部 Python。

源码：
- https://github.com/farion1231/cc-switch/blob/cdca0984231021c4eb7e8978af8670cd29c6e415/src-tauri/Cargo.toml
- https://github.com/farion1231/cc-switch/blob/cdca0984231021c4eb7e8978af8670cd29c6e415/src-tauri/tauri.conf.json

CSSwitch Next 已有 Python 代理、登录与会话迁移模块。当前采用相同的自包含发布原则，但实现不同：将匹配 Apple Silicon / Intel 的 Python、SQLite、加密库、CA 证书装入 app，Rust 统一校验和调用。不会让用户通过 pip 修补系统环境。此处没有声称已将这些 Python 模块移植成 Rust。

## 账号切换

CC Switch 为 Claude Code 准备独立的 CLAUDE_CONFIG_DIR，并在原生刷新/存储锁保护下移动凭据、更新 profile、验证及事务恢复；运行中的 Claude Code 能重新读取登录。共享目录使用链接及配置镜像。

源码：
- https://github.com/farion1231/cc-switch/blob/cdca0984231021c4eb7e8978af8670cd29c6e415/src-tauri/src/services/claude_account/snapshot/switch.rs
- https://github.com/farion1231/cc-switch/blob/cdca0984231021c4eb7e8978af8670cd29c6e415/src-tauri/src/services/claude_account/shared.rs
- https://github.com/farion1231/cc-switch/blob/cdca0984231021c4eb7e8978af8670cd29c6e415/docs/release-notes/v4.0.21-zh.md

Science 0.1.72 的原生登录与组织数据库绑定，跨组织登录变化会要求重启。CSSwitch 因此区分：

1. 优先消耗：请求级选择，不重启、不改工作区登录。
2. 同步 Science 登录：备份并校验会话后真实切换，重启一次，校验登录并恢复订阅路由。运行中任务会阻止此操作。

CC Switch 的额度格式函数与布局改编来源、MIT 许可见 THIRD_PARTY.md。针对 Science 的登录适配代码没有冒充来自 Claude Code。

## 会话共享与磁盘占用（2026-11-10 开发版）

重新核对 [CC Switch shared.rs](https://github.com/farion1231/cc-switch/blob/main/src-tauri/src/services/claude_account/shared.rs)：`projects`、`file-history`、`todos`、`plans` 使用目录链接；非空真实目录保持原样，不会每次切换复制历史。

CSSwitch 采用同一份工作区与独立登录槽的设计，但 Science 0.1.72 的目录保护拒绝活动组织路径为符号链接（隔离实测错误为 `refusing to tighten through non-directory data-dir component`）。因此活动目录通过同盘重命名交接给目标组织，其他已登记组织链接到当前真实目录以保留旧绝对路径。首次遇到独立历史才合并，成功后不再每次生成整份会话快照。

macOS 的真实 Science 隔离测试已验证不同合成账号/组织之间的项目与会话接续、新建项目切回可见。随后按用户要求完成现有两个真实账号的四次切换：首次合并之后三轮无新增会话快照，历史消息与分支保留，最终恢复原账号。一次模型回复被官方过滤拒绝，未计为模型回复成功。Windows junction 行为尚待 Windows 实测。实现与备份边界见 [订阅说明](SUBSCRIPTIONS.md)。
