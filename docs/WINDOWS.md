# Windows 版本

CSSwitch Next 同时支持 macOS 和 Windows x64。Windows 版保留相同的 Tauri 界面、连接配置、协议代理、MCP、Skills、会话、用量与汉化功能，并同步 v0.8.8 的统一提供商表单和实时版本检测。

## 本机使用

从 [Releases](https://github.com/Vonfre/CSSwitch-Next/releases/latest) 下载 `windows-x64-setup.exe` 安装包，或解压整个 `windows-x64.zip` 便携包并双击 `CSSwitch Next.exe`。不要只移动 EXE；同目录的 `proxy`、`scripts` 和 `runtime` 都需要保留。Windows 包附带 Python 3.14.0 和 Node.js 24.14.0，不需要另行配置这两项依赖。界面需要 Microsoft Edge WebView2 Runtime，NSIS 安装程序可按需安装。

1. 在“提供商”中添加 API 地址、协议、密钥和默认/质量模型，保存并测试连接。
2. 在“设置”中运行兼容性检查，确认本机 Claude Science 和运行资源。
3. 从 CSSwitch 启动第三方 Science；切换前结束正在执行的研究任务并关闭原 Science 窗口。
4. 在 MCP 管理中检查连接器；在设置中按需开启汉化和本地文件打开。

默认识别 `%LOCALAPPDATA%\Programs\ClaudeScience\claude-science.exe`，优先使用 `%USERPROFILE%\.claude-science\bin\claude-science.exe`。自定义安装可设置 `CSSWITCH_SCIENCE_BIN` 为实际 EXE 路径。

配置位于 `%USERPROFILE%\.csswitch-next`；可用 `CSSWITCH_DATA_DIR` 指定独立目录。密钥只保存在本机，以 NTFS 私有 ACL 限制访问。API 字段保持空白，由用户填写。原有 Science 数据继续位于原目录；同时支持根目录单数据库和旧版按组织分目录的数据库布局。

## 构建

需要 Windows 10/11 x64、Visual Studio C++ Build Tools 与 Windows SDK、Rust MSVC 工具链、Node.js/npm。准备独立 Python 3.11+ 环境用于测试。

```powershell
python scripts/prepare-windows-runtime.py
cd desktop
npm ci
npm run prepare:assets
npm run check
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-windows.ps1
npm run build:windows
```

`build:windows` 使用 `tauri.windows.conf.json` 生成当前用户安装的 NSIS 包。正式发布由 GitHub Actions 使用 `TAURI_SIGNING_PRIVATE_KEY` 仓库密钥生成更新签名。无签名密钥的本地构建可追加 `-- --config '{"bundle":{"createUpdaterArtifacts":false}}'`。不要更改现有更新公钥。

`scripts/prepare-windows-runtime.py` 下载固定版本并校验 SHA-256，运行库位于被 Git 忽略的 `.runtime/`。构建后可用以下命令制作便携包（输出目录必须为空）：

```powershell
python scripts/package-windows.py --binary desktop/src-tauri/target/release/desktop.exe --runtime .runtime --output release-assets/CSSwitch-Next-VERSION-windows-x64
```

将 `VERSION` 替换为 `desktop/package.json` 中的版本。备用 Inno Setup 脚本接受 `/DAppVersion=VERSION /DAppSource=完整便携目录`，正式发布使用 NSIS。发布流程为 macOS 两个架构和 Windows x64 统一生成 `latest.json`、版本绑定签名和 SHA-256 校验文件。Windows 应用内更新使用 NSIS 安装包；便携用户建议下载 ZIP 更新，避免转换为安装版。更新签名不等于 Windows Authenticode 签名。

## 验证边界

迁移期间使用本机 Science 0.1.71 的运行资源，只读核对了 24 个连接器资源以及现有会话解析。代理协议、权限、页面插件和界面通过自动测试验证。仓库所有者已完成 Windows 原生版本的本机使用验证并确认无问题。新安装的 API 由用户自行在界面填写；资源检查不代表所有远程 MCP 或 API 均已连接。

Windows 使用系统随机源、NTFS ACL、隐藏后台进程和资源管理器/记事本打开文件；macOS 保留原来的平台行为。Windows 不依赖 Bash、zsh、pkill 或 macOS 的 `open` 命令。Windows 无符号链接权限时，Skills 自动部署使用原有的复制回退机制。
