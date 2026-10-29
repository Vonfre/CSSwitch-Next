# Science 自动汉化

CSSwitch 在本地 Science 浏览器入口中汉化导航、菜单、设置和界面提示，保留聊天、文档、代码、项目名称和输入内容。关闭汉化会恢复仍由插件维护的原文。

## 开源方案调研

2026-10-28 核对 GitHub 仓库、许可证和实现：

| 项目 | 许可证 | 适用方向 | 本项目采用方式 |
| --- | --- | --- | --- |
| [translate.js](https://github.com/xnx3/translate) | MIT | 网页自动翻译、动态内容、翻译服务适配 | 将 client.edge 批量翻译服务适配为小型 Python 模块 |
| [KISS Translator](https://github.com/fishjar/kiss-translator) | GPL-3.0 | 浏览器扩展、油猴脚本、站点规则、多翻译服务 | 参考范围排除与批量缓存的设计，未复制源码 |
| [Read Frog](https://github.com/mengxi-ream/read-frog) | GPL-3.0 / 商业双许可 | 网页双语阅读、AI 翻译、请求队列 | 功能对照，未复制源码 |
| [沉浸式翻译](https://github.com/immersive-translate/immersive-translate) | 仓库未标注可复用的开源许可证 | 现成浏览器产品 | 未纳入可直接复制的源码依赖 |

translate.js 参考版本固定为 `df9d77c85399470d10e2cb610f17e294ac107740` 的 `translate.service.edge`。小型适配器在 `scripts/translatejs_edge.py`；MIT 声明随应用保存在 `scripts/licenses/translate.js-MIT.txt`。不加载远程脚本，不注入整个浏览器扩展，也不自动下载上游代码。

## 翻译方式

设置 → 通用 → Science 页面汉化 → 翻译方式：

- **本地翻译**：使用已有准确译文和此前生成的本机缓存，不发出在线翻译请求。
- **Edge 自动翻译**：新增界面文案使用 translate.js 所采用的 Edge 通道，无需模型 Key，不消耗用户的模型 Token。微软服务的可用性、限流与接口变更不由 CSSwitch 控制。
- **AI 自动翻译**：使用当前 CSSwitch 连接的默认模型及对应协议。会消耗 Token，用量计入本机记录。

旧配置默认不启用联网；两种在线方式均不会失败后自动切换到另一种服务。已有人工译文优先；未来新界面文案自动发现、批量翻译和缓存，无需先维护词库。

## 数据范围与缓存

页面只收集界面控件中的候选文案。服务端还会与本机 Science 返回的静态 JavaScript 字符串核对，只有匹配原版界面资源的文本才可离开本机。聊天 API、文件和用户输入不用于建立允许列表。浏览器不接收提供商 Key，翻译请求保留本地登录和来源校验。

每批最多 24 项、4000 字符。多个页面入口共享文件锁和权限为 0600 的 `~/.csswitch-next/science-translations/zh-CN.json` 缓存，每天最多尝试翻译 400 项新文案，缓存最多 4000 项。并发页面不重复请求同一批翻译。接口失败后退避，继续显示缓存或英文；结果必须满足数量、纯文本和占位符校验。

自动校验能发现漏译、响应格式和占位符问题，不能证明每个词在所有语境下都准确。重要术语继续使用已校对译文，模型和连接器名称保留原文。

## 本地验证

`python3 -B scripts/probe-science-ui.py --translation --audit-output artifacts/science-translation-audit.json` 使用隔离 Science 和真实 Edge 翻译通道，模拟新界面标签，检查自动汉化、私有文本过滤、缓存、关闭联网后的保留及恢复英文；不发送模型推理请求。隔离沙箱在停止后清理。
