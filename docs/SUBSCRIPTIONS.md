# Claude Science 账号切换与 Resume

CSSwitch Next 0.8.23 支持在 macOS 和 Windows 切换 Science 的真实登录账号。切换完成后核对 Science 返回的目标身份，模型请求由 Science 直接发送到官方服务。账号管理独立于其他软件。

## 使用

1. 先在官方 Science 完成一次登录并打开原会话，再在「订阅账号」添加两个官方账号。
2. 点击目标账号「切换登录」。Science 窗口和后台服务会暂时退出；正在进行的对话将中断。
3. CSSwitch 备份当前登录及完整组织会话目录，将最新的本机会话快照交给目标账号，启动 Science，并通过 Science 自身 `/api/auth/status` 核对账号、组织、邮箱。
4. 如出现本机 `Sign in` 页面，点击以重新连接后台服务。它不是重新授权官方账号。
5. 打开原项目、原会话，点击 **Resume**。不会自动重跑用户的科研计算。

macOS 适配 Science **0.1.72**，Windows 适配 **0.1.71 / 0.1.72** 的组织目录、Fernet / v2 OAuth 凭据布局；切换前检查实际凭据，未知版本或格式拒绝写入。Windows 支持配置中的自定义数据和认证目录。macOS 仍限默认目录。含符号链接、Windows junction 或特殊文件的会话目录不自动迁移；根目录单数据库可读取会话，但原生切换仍要求组织目录布局。

## 数据与凭据

- 切换前停止 Science，合并 SQLite WAL，检查数据库，再保存独立快照（macOS 使用 APFS 克隆，Windows 使用保留元数据的复制）。项目、会话 ID、消息、工具结果及 Resume 状态完整保留，不使用会清理部分执行状态的普通导入器。
- 同一账号的有效刷新凭据只由 Science 管理。首次转交后 CSSwitch 从账号池移除该账号的访问/刷新令牌；后续切换在 Science 停止后保存最新的加密原生登录，包含刷新后的令牌。订阅网关不再参与推理。
- 目标组织必须为空，或是 CSSwitch 上次保存且没有独立变化的历史。发现其他历史或外部修改会中止，不进行覆盖合并。
- macOS 备份位于 `~/.csswitch-next/subscriptions/native/backups/`；Windows 备份位于实际数据目录下的 `.csswitch-subscription-backups/`，使暂存、发布和回滚始终在同一磁盘。界面会显示恢复目录。切换日志仍在 `~/.csswitch-next/subscriptions/native/switch.json`。
- macOS 使用私有父目录 0700、凭据文件 0600；Windows 使用 NTFS 私有 ACL，CSSwitch 账户库的 AES 密钥额外使用当前 Windows 用户的 DPAPI 保护。不会改写 Science 的 `encryption.key.dpapi`。Windows 安装包自带加密依赖，无需配置 Python。
- 失败时尝试恢复原登录，保留失败现场。进程中断导致事务未完成时，订阅页提供「恢复原账号」。恢复会保留切换前后的快照。
- 不修改 Science 应用代码，不替换邮箱显示文本，也不伪造授权身份。使用用户已完成官方 OAuth 授权的真实凭据。

## Resume 的范围

Resume 可继续已有上下文。内存中的 Python/R 对象、当前本地进程、SSH/云端计算和账号专属云端资源，不保证能随账号迁移或从任意中断位置继续；这些取决于 Science 的恢复能力。没有自动重复执行工具来掩盖失败。

2026-11-08 已在 macOS Science 0.1.72 上实测：原测试会话停止并出现 Resume 后，切换另一个已授权 Pro 账号，Science 菜单显示目标邮箱；原会话 ID 和 Resume 保留。恢复后完成无工具调用的 1 至 100 累加任务，返回 **5050**。随后往返切换两个账号，测试会话消息摘要和已完成状态保持一致。此验证不包括 Python/R 计算进程或远程任务恢复。

## 测试

Windows 使用合成账号、真实加密文件和 SQLite 验证 OAuth 回调、往返切换、Resume 状态、刷新凭据所有权、失败回滚和中断恢复；只读本机检查能识别原有 11 条会话。尚未在 Windows 上完成两个真实官方账号的授权与切换实测；下述 macOS 实测不能替代这一验证。测试不会切换用户的真实账号或重跑研究任务。

```sh
PYTHON="$HOME/.claude-science/conda/envs/claude-science-mcp/bin/python3"
"$PYTHON" -I -m unittest discover -s scripts/tests -p test_science_native_switch.py -v
"$PYTHON" -I -m unittest discover -s scripts/tests -p test_science_subscriptions.py -v
"$PYTHON" -I -m unittest discover -s test -p test_subscription_gateway.py -v
cd desktop
npm run test:ui -- tests/subscriptions.spec.js tests/workbench.spec.js
```

命令行入口：`science-subscriptions.py status`、`switch ACCOUNT_ID`、`recover`。不要同时手工修改原生登录文件。

## 旧版行为

0.8.16 及以前仅切换推理网关使用的订阅账号，Science 登录身份与邮箱保持不变。该行为并不等价于完整登录切换。0.8.17 的账号按钮改为真实登录切换；切换完成后禁用旧网关推理选择与自动接管。
