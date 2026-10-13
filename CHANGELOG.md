# 更新记录

## 0.6.0

- 参考 cc-switch 优化管理工作空间：灰白 / 深色中性界面、橙色操作、统一卡片、筛选栏和窄窗口导航。
- MCP 增加搜索、来源 / 绑定状态筛选、Filesystem / Memory / Fetch 预设及标准 mcpServers JSON 导入。
- 新增 Skills 管理：Science 目录和内容预览、启停 / 卸载、本地及 GitHub 仓库发现、完整技能包导入。
- 用量统计增加每日趋势、模型汇总、缓存用量、筛选 / 分页、CSV 导出和持久化自定义模型定价。
- 新增 Science 本地会话阅读、搜索、分组、分页和 Markdown 导出；收藏、归档和显示名称独立保存，原始历史只读。

0.5.x 可在「设置 → 应用更新」直接升级；0.4.x 请下载本版安装包。安装前请结束研究任务并停止网关与 Science。

macOS：arm64 适用于 Apple Silicon，x64 适用于 Intel。应用采用 ad-hoc 签名，尚未通过 Apple 公证。Science Skills 目录不可用时，导入和卸载会暂停，页面会提示原因；会话读取不会修改 Science 原始历史。用量统计覆盖 CSSwitch 网关请求，费用按用户配置的模型价格估算。

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
