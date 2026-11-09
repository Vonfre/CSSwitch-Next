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
