# CSSwitch MCP 管理

在 CSSwitch 的“提供商”页启动第三方 Science，然后进入“MCP 管理” → “安装科研数据库服务” → “安装并绑定”。需要 Python 3 和 Node.js 22 或以上。

科研服务移植自 [OpenScience](https://github.com/synthetic-sciences/openscience/tree/a7b9fa3a8c1bd52a9a0c171de58bc14759a0f850/backend/cli/src/science/connectors)，保留 Apache-2.0 许可和来源。源码在 `mcp/openscience`，构建时生成 `scripts/openscience-mcp/server.mjs`，依赖均打入单文件。

## 对话中使用

可以在 Science 中说：“使用 csswitch-openscience 检索 PubMed 中 BRCA1 与乳腺癌的文献，给出 PMID 和链接”，或者“使用这个 MCP 查询 UniProt P04637，报告基因名与物种”。

Science 会生成 `mcp-csswitch-openscience` skill，模型加载后通过 `repl` 的 `host.mcp` 调用：

```python
catalog = host.mcp("csswitch-openscience", "science_list_dbs")
print(catalog)
papers = host.mcp("csswitch-openscience", "science_search",
                 db="pubmed", query="BRCA1 breast cancer", limit=3)
print(papers)
protein = host.mcp("csswitch-openscience", "science_fetch", db="uniprot", id="P04637")
print(protein["record"]["genes"])
```

应先加载对应 skill。`host.mcp` 在控制平面的 `repl` 中可用，不能直接放到普通 Python / R 科研执行工具。大记录只打印所需字段；Science 可能把完整打印结果保存为工具输出文件，需按其指引读取。模型提供商需要正确支持工具调用与多轮工具结果。

43 个数据库分组为蛋白/结构、基因组、化学、通路、文献与组学，包括 UniProt、RCSB PDB、AlphaFold、Ensembl、ClinVar、ChEMBL、PubChem、Reactome、KEGG、PubMed、Europe PMC、arXiv、GEO、GTEx 等。完整 ID、说明与支持的文件格式由 `science_list_dbs` 返回。注册不保证每个服务实时可用，服务可能限流、维护或要求独立凭证。

安装表单可填写 JSON 环境变量，例如 `BIOGRID_ACCESS_KEY`、`OPENALEX_API_KEY`、`SEMANTIC_SCHOLAR_API_KEY`，以及联系邮箱 `OPENALEX_MAILTO` / `CROSSREF_MAILTO`。未填所需 Key 时会返回配置错误，不会伪造空查询结果。后续更改 Key 需要删除并重新添加服务，保存值不会在 CSSwitch 回显。

## 自定义 MCP

“添加 MCP”支持本地 stdio（可执行文件绝对路径、JSON 参数数组、环境变量）或公共 HTTPS（Streamable HTTP / SSE）。添加后可绑定 OPERON，也可先取消绑定选项，授权和查看工具后再绑定。组织策略禁止时无法添加；CSSwitch 不绕过 Science 策略。

远程服务使用自己的账号授权。“授权”会让 Science 发起服务商 OAuth 并在浏览器打开，完成后点击刷新。需要 client secret 的服务可使用符合该服务要求的本地 MCP 客户端或在 Science 中完成其完整配置；本页没有任意密钥请求头输入，`headers_helper` 沿用 Science 的命令配置。Claude 的目录登录无法替代这些服务商的授权。

列表中的“工具”显示实际发现结果和权限，可以设置每次询问、总是允许、禁止或恢复默认审批。新安装保留默认审批；内置服务的“启用/停用”和自定义服务的“绑定/解绑”各自使用正确接口。删除自定义 MCP 会移除保存的连接和授权，并解除所有代理绑定。

科研服务经过 CSSwitch 网关访问公开数据库。Science 会清除子进程继承的代理变量，因此安装时会显式保存当前网关地址。更改网关端口或移动 App 后，应重新安装科研服务。手动添加其他 stdio 程序时，可在环境变量中设置其支持的 `HTTPS_PROXY`，指向 CSSwitch 网关（默认 `http://127.0.0.1:18992`）。

官方 Directory 的 HTTP 401 可以继续出现；自定义 MCP 有独立的连接状态。外部账号、租户权限、费用和配额仍由相应服务商管理。

## 开发验证

```sh
cd desktop
npm ci
npm run prepare:assets
npm run check
npm run test:ui
cd ..
python3 -m unittest discover -s scripts/tests -v
python3 scripts/probe-science-connectors.py
```

`probe-science-connectors.py` 使用安装的 Science、独立虚拟账号、本地模拟模型和仅允许 NCBI/UniProt 的 HTTPS 隧道，验证真实 MCP 执行和真实数据响应。不会切换当前配置，结果保留在忽略提交的 `.sandbox/connector-probe-*`，最终停止测试 Science。测试中的 `sandbox.hard_fail=false` 只用于 macOS 嵌套 sandbox-exec 限制；产品配置不改变。`--prototype` 可运行此前的最小 Python 原型。

已验证 Science 0.1.60 中完整的数据库工具调用与管理链路。推理使用模拟模型，不代表已经验证所有第三方模型或 43 个服务的实时可用性。离线测试还覆盖 stdio 工具协议、代理请求、文件格式、参数错误、来源故障与拒绝私网重定向，以及 nonce/cookie/CSRF、密钥不回显和已有名称保护。
