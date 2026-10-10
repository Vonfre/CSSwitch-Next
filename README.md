# CSSwitch Next

沿用 CSSwitch 的 Tauri 桌面窗口、第三方 / 官方模式、一键启动 Claude Science。第三方模式免官方登录，直接使用本机原有对话记录；支持多 API 连接、自定义模型、请求间热切换、三种协议、用量与日志。

## 使用

在 [GitHub Releases](https://github.com/Vonfre/CSSwitch-Next/releases/latest) 下载 DMG：Apple Silicon 选择 `arm64`，Intel Mac 选择 `x64`。本地构建的安装包位于 `desktop/src-tauri/target/release/bundle/dmg/`。

1. 本机安装官方 Claude Science 和 Python 3。
2. 首次打开提供商列表为空，不自动添加 DeepSeek 或通义千问。提供商总览支持搜索、状态筛选、名称排序、直接切换和独立测试。点击「添加提供商」，先搜索并选择服务商预设；也可以选择自定义 API。模板只在添加时使用。
3. 配置弹窗的「连接」页填写名称、协议、API 地址和自己的 API Key；「模型」页获取模型目录，通过模型输入旁的列表图标搜索并选择，也可直接输入自定义 ID。已保存密钥的连接首次打开时自动获取目录，默认/质量/快速模型分别选择，质量与快速可继承默认。
4. 「测试」页可直接测试尚未保存的草稿，不改写配置、不切换活动提供商。测试发送一个最小模型请求，可能产生少量费用。修改任何配置后，草稿测试结果失效；总览测试显示成功/失败和延迟。API 地址或协议改变时，不会将已存密钥静默发送到新端点，需重新输入密钥或先保存。
5. 「保存连接」只保存；「保存并使用」保存后切换。复制配置不复制密钥，关闭未保存配置会确认。结束当前研究任务并关闭原来的 Science 窗口，再「一键开始」。首次接管会重启同一个数据目录的 Science。启动后切换 API 不重启 Science，新请求使用新连接，已发送请求保留原连接。

模板包括 DeepSeek、通义千问、OpenRouter、OpenAI Responses、Anthropic 和 Ollama。模板中的模型名称需以供应商实际支持为准；模型 ID 可从 API 返回的目录选择，也可手动填写。Ollama 本地模式可填 `ollama` 作为兼容 API Key。

模型目录使用所选 API 的 `/models` 接口，不发送对话或推理请求；Anthropic 使用原生认证和分页，DeepSeek 的 Anthropic 适配地址转用其公开模型目录端点。服务商未实现目录、无权限或网络失败时保留手动输入，不猜测模型可用性。获取列表不保存草稿、不改变活动连接；已保存密钥不会被静默发送到变更后的 API 地址或协议。

## 协议

| 协议 | 处理方式 | 功能 |
| --- | --- | --- |
| Anthropic Messages | 原生透传并映射模型、替换鉴权 | 流式、工具、原生 thinking |
| OpenAI Chat Completions | 双向协议转换 | 真流式、工具增量参数、图像输入 |
| OpenAI Responses | 双向协议转换 | 真流式、function_call/output、图像输入 |

质量模型对应 Science 的 Opus 路由，默认模型对应 Sonnet，快速模型对应 Haiku。留空的质量 / 快速模型继承默认模型。网关提供稳定的 `claude-*` 模型别名。

兼容层不承诺所有供应商的扩展特性。Chat/Responses 不透传 Anthropic thinking；目前不支持 PDF/document 等非图像内容块或含图像的工具返回。Responses 不支持 `stop_sequences`，遇到这些内容会明确返回错误。官方远程 MCP / 目录连接器仍需自己的官方授权；第三方模型可调用不代表这些服务可用。

## 本地数据

- 配置：`~/.csswitch-next/config.json`，目录 0700、文件 0600，API Key 明文存储，界面只返回掩码。
- 日志：`~/.csswitch-next/logs/`；网关日志不保存提示词，查看时再次脱敏。Science 自身日志仍可能包含项目内容，请自行检查后再分享。
- 用量：`~/.csswitch-next/usage.jsonl`，仅连接 ID、模型、时间、Tokens、耗时和状态；最多保留约 4 MB，页面显示最近 2000 条。未提供用量的上游可能显示 0；不是账单或余额查询。
- 对话：直接使用 `~/.claude-science`，或官方 `config.toml` 配置的数据目录。不复制对话、不修改数据库结构，沿用原来的组织与账号标识。没有官方登录也能启动第三方模式。
- 免登录认证：`~/.csswitch-next/shared-science/auth`。不解密、不复制、不改写官方 OAuth 令牌。只读组织设置和令牌文件名；登出后可只读检查项目所属账号。为保持已有加密用户/MCP 数据兼容，复制两项数据加密密钥到权限 0600 的本地认证文件，不复制 OAuth/JWT 密钥。进程级 `security` shim 禁止认证钥匙串访问，不改变系统钥匙串设置。
- 旧隔离目录：新版不接管或自动删除。可用离线迁移脚本合并历史；迁移前必须停止全部 Science 服务、备份原目录并核验结果。备份和报告只保存在本机，不上传到仓库。
- 默认端口：网关 18992、Science 8991；Science 也可改用 8765，网关不能占用 8765。

退出应用停止网关并保留共享 Science；再次一键开始可恢复网关。切到官方模式会停止 CSSwitch 启动的共享 Science，再通过系统方式打开官方 Science，恢复原官方认证。切换模式或停止服务会中断运行中的研究任务。官方和第三方不能同时操作同一数据目录。

有多个组织且无法确定活动组织、无法确认历史所属账号、正在迁移数据目录或使用自定义认证目录时，会明确中止，不猜测历史归属。无历史的新安装会创建本地虚拟组织；以后首次官方登录产生的组织不自动合并。

第三方模式允许 Science 自带的后台自动更新：不传 `--no-auto-update`，仅在私有启动配置中设 `[update].auto_update = true`，保留原更新渠道和检查间隔，不改写官方配置。优先使用官方目录内自更新后的原生可执行文件，避免重启时退回 App 内较旧的内置版本。更新下载仍由 Science 自身执行并校验；是否可下载取决于网络与官方发布状态。集成测试继续禁用自动更新，避免测试改变已安装的 Science。

Science 的本地虚拟登录和启动行为继承 CSSwitch；不提供官方账号权限，也不保证更新后的 Science 版本保持兼容。macOS 构建为 ad-hoc 签名，未做 Apple 公证，首次打开可能需要在系统设置的「隐私与安全性」中确认。不要关闭系统安全保护。

## 开发

需要 macOS、Xcode Command Line Tools、Rust、Node.js/npm 和 Python 3。发布版本校验使用 Python 3.11+（CI 自动安装 Python 3.12）；网关运行不需要升级本机 Python。运行安装包不需要 Node.js。

```bash
npm ci --prefix desktop
npm run prepare:assets --prefix desktop
npm run dev --prefix desktop
```

```bash
npm run build --prefix desktop
```

## 自动发布

GitHub Actions 在推送 `v*` 标签或发布 GitHub Release 时，从对应标签的源码构建 Apple Silicon / Intel 两种 DMG 和 App ZIP，并上传到该 Release，同时生成 `SHA256SUMS.txt`。也可以在 Actions 的 `Build and Release` 页面手动选择已存在的标签重试。仅上传源码和构建产物，不包含本机配置、密钥或对话数据；无需配置 Apple 开发者密钥即可构建 ad-hoc 签名版本。

发布前统一修改 `desktop/package.json`、`desktop/package-lock.json`、`desktop/src-tauri/Cargo.toml`、`desktop/src-tauri/Cargo.lock`、`desktop/src-tauri/tauri.conf.json` 的版本，提交并推送源码，再打同版本标签，例如：

```bash
git tag v0.4.1
git push origin v0.4.1
```

流程会检查标签与版本一致性，执行 Python/Node/Shell、Rust 和 Playwright 测试；测试或任一架构打包失败时不会自动创建正式 Release。DMG 完整性和 App 签名在上传前检查。普通 `main` 推送和 Pull Request 只运行测试，不发布应用。

浏览器静态预览：打开 `desktop/src/index.html`，或 `npm run preview --prefix desktop` 后访问 `http://127.0.0.1:4178`。预览不控制桌面服务，实际操作必须在 Tauri 应用中执行。

## 验证

```bash
bash test/run_all.sh
cargo test --manifest-path desktop/src-tauri/Cargo.toml --lib
npm exec --prefix desktop -- playwright install chromium
npm run test:ui --prefix desktop
```

0.4.0 已验证：63 个默认 Rust 测试、43 个 Python 测试、5 个 Node 测试及 Shell 护栏与运维检查，包括自动更新、模型目录、未保存配置测试、凭据复用限制及三协议最小测试请求。10 个 Playwright 场景通过，覆盖提供商切换/筛选/复制/删除、预设选择、草稿保护、键盘操作、测试失效、模型搜索、过期目录响应和 1200/980/720/375 像素布局。0.3.3 已用本机自更新的 Science 0.1.60、临时 HOME、合成项目/会话验证共享数据、独立免登录认证、切回原认证、官方登出后仍可读原会话和认证文件不变；测试网络限制为回环，不执行真实科研任务。该集成测试单独运行：

```bash
cargo test --manifest-path desktop/src-tauri/Cargo.toml --lib installed_science_preserves_history -- --ignored
```

协议联调用本地模拟上游，未使用真实供应商密钥。Playwright 的桌面桥与用量为测试数据，截图不代表供应商实际连接状态。

已在 macOS 原生 App 核验模型目录、模型搜索、共享历史统计和升级后配置保持不变。资源 Python 使用 `-B` 禁止写入 App 内缓存，运行后 ad-hoc 签名仍通过校验。DMG 完整性校验通过；发布安装包的 SHA-256 见对应 Release 的 `SHA256SUMS.txt`。

0.4.1 去掉默认内置连接和不可删除限制；旧格式中实际保存的密钥仍兼容，但不存在的连接不再自动补回。所有连接可删除，包括停止服务后的当前/最后一条；删除当前连接会清空当前选择，不擅自切换到另一家。运行中的当前连接须先停止服务才能删除。已有用户配置不会被升级清空。65 个默认 Rust 测试与 12 个界面测试通过，覆盖全空初始化、删除后刷新不复活、最后一条删除、空配置修改端口与运行中删除保护。

## 来源

代码底座继承 [visail/CSswitch](https://github.com/visail/CSswitch)，原始代码保留 [MIT 许可证](LICENSE-MIT)，本项目新增代码沿用目标仓库的 [Apache-2.0 许可证](LICENSE)。设计参考 [sylearn/AIUsage](https://github.com/sylearn/AIUsage) 的固定网关和逐请求归属、[Vonfre/claude-science-api](https://github.com/Vonfre/claude-science-api) 的连接验证与三协议，以及 [farion1231/cc-switch](https://github.com/farion1231/cc-switch) 的提供商预设、列表操作和可搜索模型选择流程。未移植 cc-switch 的 React 组件源码。研究记录见 [docs/RESEARCH.md](docs/RESEARCH.md)，版权归属见 [NOTICE](NOTICE)。
