# 应用内更新

CSSwitch Next 0.5.0 起启动时自动检查 GitHub 的最新稳定 Release，设置中的「应用更新」也可以手动检查。检查失败会保留可重试状态，不会停止服务。

点击「下载更新」后在后台下载与当前应用架构对应的更新包，使用应用内置公钥验证 Minisign 签名。下载成功后点击「安装并重启」。请先结束研究任务，并在提供商页面停止网关与 Science；应用也会在原生安装入口检查网关进程，防止绕过界面检查。

macOS 应用必须复制到本机可安装目录，不能在挂载的 DMG 中直接更新。如果目录需要管理员权限，macOS 会显示系统授权提示。Windows 0.8.9 起使用当前用户 NSIS 安装包更新；便携版建议下载新版 ZIP 更新，避免转换为安装版。应用配置保存在原有用户目录，Science 对话记录也继续保留。更新不需要 claude.ai 会话，不使用提供商的 API Key。联网使用系统/环境代理，GitHub 无法访问时可配置系统代理并重试。

0.5.1 起，GitHub 下载域名连接失败时，会自动通过公开 API 获取最新 Release 的更新清单和对应架构的更新包，不需要 GitHub 登录或 Token。备用路径继续使用同一公钥验证签名和版本；两个路径都不可达时会提示错误并允许重试。

旧版没有更新模块，因此 0.4.2 首次升级需要手动安装最新稳定版。安装一次之后，后续版本可以在应用内更新。

## 发布维护

1. 同步 package.json、package-lock.json、Cargo.toml、Cargo.lock、tauri.conf.json 中的版本，并在 CHANGELOG.md 添加同版本的更新说明。
2. 推送 main 及对应 vX.Y.Z 标签。GitHub Actions 验证版本、运行测试、构建 macOS arm64/x64 安装包和更新包，以及 Windows x64 NSIS 安装包和便携 ZIP。
3. 构建任务使用仓库 Secret `TAURI_SIGNING_PRIVATE_KEY` 签名，macOS 任务另外使用隔离的应用副本验证下载、损坏包拒绝、版本签名校验和原生安装。发布任务用仓库公钥验证三个平台目标的签名和版本绑定，再生成 latest.json 和 SHA256SUMS.txt，在全部附件上传成功后发布 Release。Windows 更新签名独立于 Authenticode 代码签名。

更新私钥保存在开发者机器仓库外的 `~/.csswitch/release-signing/updater.key`，仅用于发布，不随应用分发。请备份并限制访问；后续版本必须使用同一个密钥才能让已安装应用验证更新。当前密钥没有密码，GitHub Secret 使用同样的私钥，构建时设置空的 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`。不要把私钥写进日志、配置模板或仓库。

Fork 项目需要生成自己的签名密钥、配置 Secret，并同时修改 tauri.conf.json 中的公钥与更新地址，以及 updater.rs 的 RELEASE_API 仓库地址。应用更新签名独立于 Apple 签名/公证；当前 macOS 包仍采用 ad-hoc 签名。
