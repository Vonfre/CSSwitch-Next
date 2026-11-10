# 环境检测（0.9.0）

页面参考 [Net.Coffee Claude 检测](https://ip.net.coffee/claude/) 的三列卡片组织方式，布局与代码在本项目独立实现。展示真实探测与有来源的第三方数据，不模拟“正常”，不承诺防封。

## 手动检测

- 只在用户点击“立即检测”或页面刷新按钮后联网。启动、打开页面、窗口聚焦与最小化均不触发检测，没有后台定时监测线程。
- 手动检测重新读取系统代理并建立连接，不受 60 秒缓存或普通网络失败退避影响。服务明确返回限频 / Retry-After 时仅暂停该端点，其他出口照常探测；全部冷却时明确提示未联网并保留原检测时间。IP 情报仍遵守自身缓存与限频。
- API、Claude 网页、Cloudflare 对照以及国内站点分别检测，国内站点的出口不预设为中国 IP。结果只代表探测进程的线路，不证明 Science 的独立代理、PAC、SOCKS、按进程分流或既有连接使用同一路径。
- 跨地区 / 多次出口变化、第三方 Tor / 滥用标记、系统时间偏差和地区名单异常显示提醒。VPN / 机房本身仅作信息提示。HTTP 403、429、连接失败不推断账号被封。
- 原有账号额度仍仅启动一次及手动刷新。本功能不调用登录、额度或推理接口，不修改账号优先级、代理或 Science 会话。

## 数据来源

- `claude.ai/cdn-cgi/trace`、`api.anthropic.com/cdn-cgi/trace`、`www.cloudflare.com/cdn-cgi/trace`：出口、地区、TLS、延迟。
- `2026.ip138.com`：国内站点访问路径的公网 IPv4。Net.Coffee geoip 提供归属地。
- `status.claude.com/api/v2/status.json`：官方公开服务状态。
- Net.Coffee 参考页面使用的公开 `api/iprisk` / `api/geoip` 数据；不可用时 IP 情报回退到 `api.ipapi.is`。校验响应 IP 与查询一致，或验证 Net.Coffee 返回的窄网段同时包含请求 IP 与样本 IP（IPv4 至少 /24、IPv6 至少 /48）；后者明确标注同网段缓存，并不视作此 IP 的独立记录。只使用明确返回的字段。信誉分仅显示来源的 0–100 有限数值，不自行编造或把分数作为封号概率。参考站点接口非官方稳定协议，变更或不可达时显示未知。
- 有效 IP 情报缓存 24 小时，最多 8 个出口；失败至少退避 15 分钟。官方支持地区来自 [Claude Help Center](https://support.claude.com/en/articles/8461763-where-can-i-access-claude)，核对日为 2026-11-10；超过 180 天显示需更新。

## 手动深度检测与隐私

- DNS：向 Net.Coffee 随机诊断域名做系统解析，再查询该随机标识对应的递归 DNS 出口。地区差异仅表示路径差异；无记录 / 未取得归属地保持未知。
- WebRTC：当前 CSSwitch WebView 通过 Cloudflare STUN 收集 UDP server-reflexive 候选，过滤非公网地址；按候选 IP 查询 Net.Coffee 归属地。与参考出口不一致仅提示差异，不声称泄露身份。浏览器禁用、候选为空或失败保持未知。
- 设备语言、时区、Cookie 能力、WebGL / Canvas 摘要只在本地页面计算，不传给网络服务，也不伪装或改变指纹；它们不等同于 Science 的浏览器环境。
- 网络服务会看到请求来源；IP 情报 / 归属地服务还会收到查询的公网 IP。没有账号邮箱、令牌、Cookie、会话正文或设备摘要被附加到请求。
- 本地 `~/.csswitch-next/environment/state.json` 使用私有权限及原子写入。最多保留 48 条 / 24 小时的出口变化，默认完整显示 IP，可通过“隐藏 IP”开关遮盖，也可手动清除记录。检测失败保留明确的未知 / 旧结果状态。
