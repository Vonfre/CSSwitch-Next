# CSSwitch MCP 管理

CSSwitch 0.8.0 接入 Science 安装包已提供的 24 个本机工具实现。当前验证版本为 Science 0.1.62；共 225 个工具。保留工具名、参数、数据源限制及默认审批，不复制或重新分发 Science 实现源码。

## 接入与使用

在提供商页启动第三方 Science 后，CSSwitch 自动核对本机资源并接入。也可以进入 MCP 管理，点击“接入 24 个本机连接器” → “接入并绑定”进行修复或配置环境变量。

20 个原生连接器已有本机实现，继续沿用原来的 ID、绑定、权限、凭据及手动启停状态。PubMed、bioRxiv、ChEMBL、Clinical Trials 的本机版本分别注册为 `csswitch-local-pubmed`、`csswitch-local-biorxiv`、`csswitch-local-chembl`、`csswitch-local-clinical-trials`，直接调用公开数据源。确认本机版本能够加载工具后，停用对应的 Claude 托管版本。模型 API 通过所选第三方网关调用，科研 MCP 不依赖 Claude 目录登录。

例如在 Science 对话里说“使用本地 PubMed 搜索 BRCA1 乳腺癌文献，给出 PMID”。模型加载相应 MCP skill 后调用：

```python
papers = host.mcp("csswitch-local-pubmed", "search_articles",
                  query="BRCA1 breast cancer", max_results=5)
trial = host.mcp("csswitch-local-clinical-trials", "get_trial_details",
                 nct_id="NCT04280705")
```

Ketcher Chemistry 继续使用原生服务 ID，保留互动编辑器及资源读取。连接器名称保持原文，按钮、状态、说明等界面文字继续汉化。

## 本机配置与凭据

需要已安装完整的 Claude Science 及其 MCP Python 环境；Ketcher Chemistry 的独立配置需要本机 Node.js。管理器会先定位完整运行资源，再将明确的服务入口路径交给 MCP 子进程，兼容 Science 沙箱对目录扫描的限制。再次启动或接入时会修复资源更新后的路径和网关端口。

原生 20 个服务的凭据继续在 Science 中管理。新注册的 4 个服务可在接入窗口设置环境变量，例如实际的 `NCBI_EMAIL` 和可选的 `NCBI_API_KEY`。PubMed 元数据获取等工具要求联系邮箱；OpenAlex 等数据源需要各自的 API 密钥。不会编造邮箱、复制原生密钥、跳过数据源的许可限制或自动授予工具权限。再次接入保留已有环境变量，并合并用户填写的新值；列表和报告不回显密钥。

点击“导出 24 个本机配置”可得到标准 `mcpServers` JSON。导出使用原始服务名，例如 `pubmed` 和 `ketcher-chemistry`；命令指向本机安装资源，适合在同一台机器的其他 MCP 客户端中导入，不包含已保存的 API Key。代理环境变量指向 CSSwitch 网关，调用公开接口时需保持网关运行。

## 工具清单

以下数字来自当前运行中的 Science 实例的工具枚举，不表示每个工具都执行了实时查询。

| 连接器 | 当前工具数 | Science 接入方式 |
| --- | ---: | --- |
| BioMart | 8 | 沿用原生本机服务 |
| Cancer Models | 6 | 沿用原生本机服务 |
| CellGuide | 5 | 沿用原生本机服务 |
| Chemistry | 12 | 沿用原生本机服务 |
| Clinical Genomics | 20 | 沿用原生本机服务 |
| Drug Regulatory | 7 | 沿用原生本机服务 |
| Expression | 12 | 沿用原生本机服务 |
| Genes & Ontologies | 7 | 沿用原生本机服务 |
| Genomes | 11 | 沿用原生本机服务 |
| Human Genetics | 12 | 沿用原生本机服务 |
| Ketcher Chemistry | 1 | 沿用原生本机服务 |
| Literature Graph | 9 | 沿用原生本机服务 |
| Omics Archives | 17 | 沿用原生本机服务 |
| Protein Annotation | 13 | 沿用原生本机服务 |
| Regulation | 10 | 沿用原生本机服务 |
| Research Resources | 5 | 沿用原生本机服务 |
| RNA | 9 | 沿用原生本机服务 |
| Structures & Interactions | 16 | 沿用原生本机服务 |
| Variants | 15 | 沿用原生本机服务 |
| ZINC | 4 | 沿用原生本机服务 |
| bioRxiv | 7 | 独立本机 stdio |
| ChEMBL | 6 | 独立本机 stdio |
| Clinical Trials | 6 | 独立本机 stdio |
| PubMed | 7 | 独立本机 stdio |

## 旧聚合服务迁移

应用不再打包或提供 csswitch-openscience 的安装入口。四个替代服务均就绪后，将旧本地 MCP 配置备份到 `~/.csswitch-next/mcp-migration-backups`（目录权限 0700、文件权限 0600），再移除原先由 CSSwitch 安装的聚合服务。部分接入失败时保留旧配置，报告具体失败项，支持重试。

## 验证

- 当前 Science 实例 24/24 服务已连接，225 个工具加载成功；重复接入没有产生重复服务。
- 全部 24 个独立进程的 MCP 握手、输入 schema、工具枚举及 Ketcher 资源读取通过。
- 真实 PubMed、ChEMBL、bioRxiv、ClinicalTrials.gov、UniProt 查询返回有效数据。
- 独立 HOME 和网络隔离的 Science 实例中，模拟第三方模型通过 skill → repl → host.mcp 完成 PubMed 与 UniProt 调用，未连接 Claude 托管 MCP。此测试不代表已测试所有第三方模型。
- 汉化及 MCP UI、管理桥接、迁移幂等性、失败保留、私密备份及版本构建通过测试。
