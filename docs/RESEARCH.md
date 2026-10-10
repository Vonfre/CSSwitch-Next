# 仓库研究与重构决策

研究时间：2026-10-09。重复提供的 claude-science-api 按一个仓库处理。

| 仓库 | 研究版本 | 具体借鉴 |
| --- | --- | --- |
| visail/CSswitch | a261f5263d3ecc38cf7616fa794d9eb5637c66aa | Tauri 窗口、配置权限与原子写、隔离启动、稳定路径鉴权、Science 模型别名 |
| sylearn/AIUsage | 5478ea457aec05908f4e433e9abaa0e4836f03f6 | GlobalProxyRuntime 的固定端点与热切换；ScienceSelectionNormalizer 的稳定模型选择；用量按连接归属 |
| Vonfre/claude-science-api | ee297cab82cee2712e9066d181234861e17bea4a | provider.rs 中公开配置与密钥分离；profile_switch.rs 的独立候选验证；三协议与健康状态合同 |
| farion1231/cc-switch | c261126630b3416e67b407da018cc614bbbe84cd | ProviderPresetSelector 的先选预设后配置、ProviderCardActions 的列表直接操作、ModelInputWithFetch/FetchedModelPicker 的输入与搜索式选择分离 |

## 保留

CSswitch 的 Rust 配置、虚拟登录、进程定位及沙箱脚本构成新版底座。使用 Tauri 桌面窗口与系统浏览器打开 Science，第三方和官方模式都保留。

## 改造

原版只允许 DeepSeek / Qwen 两种固定 provider；新版的连接含名称、协议、Base URL、默认 / 质量 / 快速模型、输出上限与独立密钥。

原版换 provider / Key 会重启代理；新版网关保留端口与路径鉴权，从原子配置文件为每个请求创建独立快照。一次请求的地址、协议、模型和密钥始终绑定到同一快照，切换不会混用新旧配置。

原版 Qwen 等待完整响应再回放 SSE；新版逐事件转换 Chat 和 Responses 流式响应，包括函数名、调用 ID、增量参数和结束原因。工具 JSON 不合法或流提前终止时返回 SSE error。

连接验证不再隐式启动或切换正式代理；独立验证进程读取指定连接，发送最小请求并校验返回结构。TCP 可达或 HTTP 200 本身不作为模型成功证据。

用量日志按请求快照记录连接 ID、实际模型和供应商报告的 Tokens；不保存对话，提供最近记录和 CSV 导出。网关、Science、模型验证分别展示。

## 0.3.1 共享历史

按用户的新要求，不再隔离 HOME/对话目录。核对本机 Science 0.1.25 的 CLI 和设置实现：`--data-dir` 决定对话存储，`--config` 的 `[paths] auth_dir` 决定认证存储，`active-org.json` 决定 `data-dir/orgs/<uuid>`。只共享目录但更换 org/account 会隐藏历史，因此必须保留原身份标识。

共享启动直接使用本机 HOME 与原数据目录；本地认证文件仍只写 CSSwitch 私有目录，虚拟登录的原目录保护不放宽。通过 Science 自己的 stop/status/url 管理唯一 daemon，按 PID、端口和私有配置路径确认归属。Science 新版会镜像密钥到 Keychain，故为免登录进程生成局部 security shim；不改系统默认钥匙串。旧隔离目录不迁移、不删除，Science 的研究执行沙箱与审批默认保持官方配置。

临时 HOME 的安装二进制测试创建合成项目/会话，切换认证存储后验证同一项目和会话返回不变，再恢复原认证。外层测试沙箱只允许回环网络，内层功能探针在嵌套沙箱中不可用，所以仅测试配置设 `sandbox.hard_fail=false`；产品启动不注入此设置。测试不代表真实科研执行或供应商兼容性证据。

## 0.4.0 提供商工作流

独立阅读 cc-switch 的提供商预设选择器、卡片操作、模型选择器和端点测试组件，借鉴交互层次而非复制 React 源码。保留当前无框架前端和 Tauri 后端；主屏改为提供商列表，连接配置独立弹窗，连接/模型/测试按页签组织。搜索和状态筛选只影响列表，不改变当前提供商；测试也不激活候选连接。

未保存配置测试由 Rust 校验 URL/协议/模型和已存密钥复用边界，通过子进程 stdin 发送临时配置，Python 复用已有三协议最小模型请求。密钥不写临时文件或 argv。保存与激活分开，激活失败时保留已保存结果。模型目录请求不锁定表单，以序列号和端点/密钥快照丢弃过期响应；选模型不保存或隐式测试。

## 未合并范围

AIUsage 的 SwiftUI、账号数据库、订阅 OAuth 池、CPA 安装器未移植。SciPort 的 Codex 登录、更新器、Skill 管理和大型 Rust gateway 未合并。新版聚焦用户要求的 CSSwitch 桌面运行方式与 Claude Science 第三方 API 切换。

真实第三方服务和完整 Science 科研任务需要用户自己的 API、模型权限及供应商兼容性，当前自动化测试使用本地模拟上游。
