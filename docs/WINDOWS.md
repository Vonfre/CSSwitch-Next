# Windows 版本

本分支以 CSSwitch Next 0.8.7 为基础复用原有 Tauri 界面、连接配置、协议代理、MCP、Skills、会话、用量与汉化功能，增加 Windows x64 平台支持。

## 本机使用

解压整个便携包，双击 `CSSwitch Next.exe`。不要只移动 EXE；同目录的 `proxy`、`scripts` 和 `runtime` 都需要保留。本次便携包附带 Python 3.14.0 和 Node.js 24.14.0，不需要另行配置这两项依赖。界面需要 Microsoft Edge WebView2 Runtime。

1. 在“提供商”中添加 API 地址、协议、密钥和默认/质量模型，保存并测试连接。
2. 在“设置”中运行兼容性检查，确认本机 Claude Science 和运行资源。
3. 从 CSSwitch 启动第三方 Science；切换前结束正在执行的研究任务并关闭原 Science 窗口。
4. 在 MCP 管理中检查连接器；在设置中按需开启汉化和本地文件打开。

默认识别 `%LOCALAPPDATA%\Programs\ClaudeScience\claude-science.exe`，优先使用 `%USERPROFILE%\.claude-science\bin\claude-science.exe`。自定义安装可设置 `CSSWITCH_SCIENCE_BIN` 为实际 EXE 路径。

配置位于 `%USERPROFILE%\.csswitch-next`；可用 `CSSWITCH_DATA_DIR` 指定独立目录。密钥只保存在本机，以 NTFS 私有 ACL 限制访问。API 字段保持空白，由用户填写。原有 Science 数据继续位于原目录；同时支持根目录单数据库和旧版按组织分目录的数据库布局。

## 构建

需要 Windows 10/11 x64、Visual Studio C++ Build Tools 与 Windows SDK、Rust MSVC 工具链、Node.js/npm。准备独立 Python 3.11+ 环境用于测试。

```powershell
cd desktop
npm ci
npm run prepare:assets
npm run check
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-windows.ps1
npm run build:windows
```

`build:windows` 使用 `tauri.windows.conf.json` 生成当前用户安装的 NSIS 包，默认不生成签名更新文件。本机源码也可用 `cargo build --release --features tauri/custom-protocol` 编译，并用 `scripts/package-windows.py` 将 EXE 与运行资源组合成便携 ZIP。

自动更新仍保留原来的签名校验。仓库目前的发布清单面向 macOS；只有发布并签名 Windows 更新包、加入 `windows-x86_64` 条目后，Windows 自动更新才能使用。本次本地构建没有发布到 GitHub，也没有使用或更改发布签名密钥。

## 验证边界

迁移期间使用本机 Science 0.1.71 的运行资源，只读核对了 24 个连接器资源以及现有会话解析。代理协议、权限、页面插件和界面通过自动测试验证。真实上游模型请求需要用户自行填写 API 后测试。完整原生 Science 启停还必须以本机实际运行验证结果为准；资源可用不等于 MCP 已连接。

Windows 使用系统随机源、NTFS ACL、隐藏后台进程和资源管理器/记事本打开文件；macOS 保留原来的平台行为。Windows 不依赖 Bash、zsh、pkill 或 macOS 的 `open` 命令。Windows 无符号链接权限时，Skills 自动部署使用原有的复制回退机制。
