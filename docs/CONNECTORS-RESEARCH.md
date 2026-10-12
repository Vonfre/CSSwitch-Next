# 第三方 API 模式下的 Science connectors 调研

调研日期：2026-10-12。OpenScience 研究版本：`a7b9fa3a8c1bd52a9a0c171de58bc14759a0f850`。

## 结论

第三方模型 API 可以与独立 MCP / 科研数据库访问并存。当前 Directory connectors 报错来自 claude.ai 的账号服务认证；模型 API Key 不提供这项认证。OpenScience 的实现提供了独立接入的参考，并不证明可以用第三方 Key 恢复 Anthropic 托管的连接器。

CSSwitch 已增加 MCP 管理页，通过 Science 已有的自定义 MCP 接口接入外部服务；OpenScience 的 43 个数据库适配器已移植为一个本地 stdio MCP 服务。需要保留官方 Directory 时，应另行支持有效的官方登录及相应网络访问。

已在独立 HOME、虚拟账号和隔离网络下测试 Science 0.1.60：自定义 MCP 策略允许，Science 对话通过 skill → repl → host.mcp 调用移植服务，PubMed 搜索和 UniProt TP53 查询返回真实数据，Directory 同时保持 HTTP 401。推理端使用本地确定性模拟模型；未验证用户实际第三方模型或各外部服务的 OAuth。未修改真实 Science 的登录、历史或运行配置。

## 当前报错的原因

CSSwitch 的第三方模式有三项相关行为：

1. `desktop/src-tauri/src/science_shared.rs` 的 `prepare()` 在 CSSwitch 私有 auth-dir 中生成本地虚拟登录；不使用原始官方 OAuth 登录。
2. `desktop/src-tauri/src/lib.rs` 启动 Science 时，将模型请求的 `ANTHROPIC_BASE_URL` 指向本地网关，同时将 HTTPS 代理指向同一个网关。
3. `proxy/csswitch_proxy.py` 的 `_BLOCKED_SUFFIXES` 和 `do_CONNECT()` 对 `anthropic.com`、`claude.ai`、`claude.com` 及其子域返回 401，以便虚拟登录的组织查询快速失败。

因此，当前模式下官方 Directory 不可用是可以预期的。取消 401 拦截后，本地虚拟令牌仍无法通过官方服务器验证；修改令牌有效期、把错误改为成功或仅显示缓存目录，也不会恢复真实的工具调用。

## 本机 Science 的官方 Directory 链路

在 `/Users/zhanghuan/.claude-science/bin/claude-science` 内嵌代码中核对到：

- 目录初始化：`/api/oauth/organizations/{org}/mcp/v2/bootstrap`，读取 SSE 的 `server_base` 和 `completed` 事件。
- 目录安装：`/api/oauth/organizations/{org}/mcp/remote_servers`，携带目录条目的 UUID。
- Directory 实际 MCP 请求：`/v1/toolbox/shttp/mcp/{server-id}`，通过官方服务转发；认证依赖官方 access token、组织标识等。
- 登录请求的 scopes 包含 `user:mcp_servers`；目录初始化也区分未登录、缺少 scope、HTTP 错误和网络错误。

这条链路超出了 `ANTHROPIC_BASE_URL` 的模型请求路由。第三方模型提供商通常不会实现这些账号、目录和工具代理接口。

## OpenScience 的两套实现

### 外部服务 MCP

源码：

- [连接器目录 catalog.ts](https://github.com/synthetic-sciences/openscience/blob/a7b9fa3a8c1bd52a9a0c171de58bc14759a0f850/backend/cli/src/mcp/catalog.ts)
- [MCP 客户端 index.ts](https://github.com/synthetic-sciences/openscience/blob/a7b9fa3a8c1bd52a9a0c171de58bc14759a0f850/backend/cli/src/mcp/index.ts)
- [OAuth provider](https://github.com/synthetic-sciences/openscience/blob/a7b9fa3a8c1bd52a9a0c171de58bc14759a0f850/backend/cli/src/mcp/oauth-provider.ts)
- [使用文档](https://github.com/synthetic-sciences/openscience/blob/a7b9fa3a8c1bd52a9a0c171de58bc14759a0f850/frontend/docs/src/content/openscience/connectors.mdx)

其客户端使用 MCP SDK，支持 Streamable HTTP、SSE 和本地 stdio。远程服务分别授权，包含 OAuth 元数据发现、PKCE、客户端注册或指定 client ID、令牌持久化与刷新。连接器认证与模型提供商配置各自独立。

目录中的例子：

| 服务 | 接入方式 | 条件 |
| --- | --- | --- |
| GitHub | `https://api.githubcopilot.com/mcp/` | 对应账号授权，可能需要组织批准 |
| Benchling | `https://<tenant>.mcp.benchling.com/mcp` | 租户提供准确地址，启用 Chat/MCP 与相应 API |
| Box | `https://mcp.box.com` | 对应企业权限、client ID/secret |
| Dropbox | 官方 Dash MCP 项目的本地服务 | 自行安装、配置凭证；目录没有提供已审核的托管地址 |

这些是 OpenScience 源码中的配置与要求，本次没有实际连接这些服务。目录卡片存在不代表用户已授权，也不代表各服务的全部功能都支持。

### 科研数据库适配器

源码：

- [数据库注册表](https://github.com/synthetic-sciences/openscience/blob/a7b9fa3a8c1bd52a9a0c171de58bc14759a0f850/backend/cli/src/science/connectors/index.ts)
- [PubMed](https://github.com/synthetic-sciences/openscience/blob/a7b9fa3a8c1bd52a9a0c171de58bc14759a0f850/backend/cli/src/science/connectors/literature/pubmed.ts)
- [UniProt](https://github.com/synthetic-sciences/openscience/blob/a7b9fa3a8c1bd52a9a0c171de58bc14759a0f850/backend/cli/src/science/connectors/proteins/uniprot.ts)
- [ChEMBL](https://github.com/synthetic-sciences/openscience/blob/a7b9fa3a8c1bd52a9a0c171de58bc14759a0f850/backend/cli/src/science/connectors/chemistry/chembl.ts)
- [模型可调用的 science 工具](https://github.com/synthetic-sciences/openscience/blob/a7b9fa3a8c1bd52a9a0c171de58bc14759a0f850/backend/cli/src/tool/science.ts)

这些适配器直接调用数据库公开 API，例如 NCBI E-utilities、`rest.uniprot.org`、EBI ChEMBL。统一通过 `science_list_dbs`、`science_search`、`science_fetch` 给模型使用；HTTP helper 提供超时、缓存、重试和限流。

它们与官方 Directory 是不同的接入链路。移植时需要提供工具执行宿主；仅复制 TypeScript 数据库适配器不能直接让官方 Science 调用。适合将所需适配器封装为本地 MCP 服务，保留来源链接和原有许可证要求。公开 API 仍受网络、配额与数据使用条件影响。

## CSSwitch 的具体落地方式

### 优先沿用 Science 自定义 MCP

本机二进制中已确认以下接口：

| 操作 | 接口 |
| --- | --- |
| 添加远程 MCP | `POST /api/mcp-servers` |
| 添加本地 stdio MCP | `POST /api/mcp-servers/local` |
| 查看连接器 | `GET /api/mcp-servers/connectors` |
| 启用或停用 Science 内置连接器 | `PUT /api/mcp-servers/connectors/{id}/enabled`（仅 bundled） |
| 查看 Directory 健康 | `GET /api/mcp-servers/directory-health` |
| 给 agent 绑定连接器 | `POST /api/agents/{name}/connectors` |
| 启动连接器授权 | `POST /api/mcp-servers/connectors/{id}/authorize` |
| 解绑自定义 MCP | `DELETE /api/agents/{name}/connectors/{id}` |
| 查看工具权限 | `GET /api/mcp-servers/{id}/tool-permissions?source=local-stdio` |
| 设置单项工具权限 | `POST /api/mcp-servers/{id}/tool-grants` |

这些请求应通过当前 Science 的受保护本地会话发送；不能将它们当成无需认证的公开接口。

远程创建接口字段包括 `name`、`url`、`transport`、`oauth_server_url`、`client_id`、`scopes`、`headers_helper`。远程 transport 为 `streamable_http` 或 `sse`。本机版本对远程地址要求公开 HTTPS；本地服务应优先使用 stdio，不能假设 `http://127.0.0.1:端口/mcp` 可以直接填入远程表单。

实施顺序：

1. 在隔离测试会话检查 `operon_custom_connectors` 策略是否允许自定义服务。本机远程和本地创建函数都检查这个能力；如果组织禁用，需要按该组织授权配置处理。
2. 用一个返回确定结果的本地 stdio MCP 验证添加、启用、工具发现、agent 绑定及真实工具调用。
3. 将 PubMed、UniProt、ChEMBL 等所需公开 API 封装为本地服务，或使用服务提供商的独立 MCP 地址并完成各自授权。
4. 验证工具定义、tool-call ID、参数和 tool-result 能经过现有三协议转换，并让模型根据返回结果完成下一轮回答。
5. CSSwitch 如增加管理页面，应分别显示 Directory 健康和自定义 MCP 健康，让用户知道哪些工具实际可用。

CSSwitch 原生侧校验正在运行的 Science 属于自身 session，获取一次性 nonce，通过 Python 管理桥接在内存完成 cookie / CSRF 认证。nonce、cookie、环境变量和 OAuth 地址不回传界面。Science 检查原有组织策略；默认安装不自动允许工具调用。自定义 stdio/HTTPS 服务通过绑定和解绑管理，不能调用 bundled 专用开关。

Science 会清除 stdio 子进程的代理环境。科研服务安装时显式保存 CSSwitch 当前回环网关地址；Node 网络层支持该代理，并限制为适配器使用的数据库域名。改变网关端口后，需要删除并重新安装科研服务，使代理配置更新。管理接口禁止重定向，避免本地认证信息被转发。

### 如必须使用官方 Directory

可另行设计“官方登录 + 第三方模型推理”的组合模式：账号与目录请求使用真实有效的官方 OAuth，模型请求继续使用本地网关。需要明确实现独立认证模式、允许必要官方域名访问、令牌刷新和缺少 scope 的恢复；不能只移除当前 CONNECT 拦截。

该模式需要用户实际拥有相应账号和服务权限。当前 CSSwitch 的虚拟认证及进程钥匙串隔离不能提供这项能力；本次没有尝试改用真实令牌，也没有验证官方 Directory 在第三方推理组合下的完整兼容性。

## 验收标准

“连接器可用”至少应包括工具成功发现、模型实际发起工具调用、服务返回有效结果、结果回传模型并被回答使用。仅目录显示、HTTP 200 或错误提示消失不算完成。

已通过移植服务的真实 PubMed/UniProt 调用与返回模型链路、受保护管理接口的添加/绑定/解绑/工具权限/删除、18 项 UI 测试，以及 9 项桥接和 MCP 合约测试。43 个适配器均已注册，但没有逐一进行实时查询；外部服务账户授权和用户的真实模型推理仍需在各自配置下验证。复现方式及使用说明见 [MCP 管理](MCP.md)。
