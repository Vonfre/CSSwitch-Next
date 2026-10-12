# 更新记录

## 0.5.1

- 保留 0.5.0 的应用内更新、MCP 管理及 43 个 OpenScience 科研数据库适配器。
- GitHub 下载域名无法访问时，检查和下载更新自动改用公开 API，无需 GitHub 登录；仍验证更新包签名及版本。

在「设置 → 应用更新」检查、下载并安装新版。旧版 0.4.2 首次升级需安装本版，后续升级可直接在应用内完成。安装前请结束研究任务并停止网关与 Science，提供商配置、密钥和对话记录保留。

macOS 安装包：arm64 适用于 Apple Silicon，x64 适用于 Intel。应用采用 ad-hoc 签名，尚未通过 Apple 公证。使用 Science 功能需要本机安装 Claude Science 和 Python 3；科研 MCP 还需要 Node.js 22+。请将应用复制到本机应用目录后运行。

## 0.5.0

- 新增应用内更新：启动时检查新版，在「设置 → 应用更新」下载、验证签名、安装并重启。
- 新增 MCP 管理：添加本地与远程 MCP、绑定 Science、管理工具权限及服务授权。
- 内置来自 OpenScience 的 43 个科研数据库适配器，提供数据库列表、检索与详情工具，独立于 claude.ai Directory 登录。
- 更新安装前检查本地网关是否停止，提供商配置、密钥和 Science 对话记录保留。

macOS 安装包：arm64 适用于 Apple Silicon，x64 适用于 Intel。旧版 0.4.2 首次升级需下载此版安装包；0.5.0 起可在应用内升级后续版本。请将应用复制到本机应用目录后运行。

应用采用 ad-hoc 签名，尚未通过 Apple 公证。使用 Science 功能需要本机安装 Claude Science 和 Python 3；科研 MCP 还需要 Node.js 22+。需要认证的科研服务仍需该服务自己的授权或 API Key。
