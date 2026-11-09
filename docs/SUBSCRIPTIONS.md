# 0.8.30：账号开关与手动刷新

每行账号的「优先消耗」开关互斥，打开一个会关闭另一个；关闭后不指定优先账号，沿用当前账号，并保留不可用时自动使用备用账号。更改仅影响下一次回复，不重启 Science、不改变登录邮箱。账号菜单只保留重新授权与删除，点击外部、Esc 或移开键盘焦点即可关闭。

额度在应用启动时查询一次，此后仅响应「刷新额度」、单账号刷新和页面刷新按钮。等待、返回页面、聚焦窗口、修改优先账号不会自动查询。30 秒本地计时器仅更新显示时间，不发送网络请求。查询使用官方用量接口，不发送模型消息；仍遵守官方查询限频与退避。

---

# 0.8.29：同步真实登录邮箱

「优先消耗」切换模型请求使用的订阅；「Science 登录邮箱」是组织工作区身份。两者不一致时，点击 **同步 Science 登录为 …** 可真实切换邮箱。此操作重启 Science 一次，完成登录校验和会话合并后恢复自动接管设置。已有账号授权可复用，官方撤销授权时仍须重新授权。

运行中的回复或任务会阻止同步操作。请先完成或暂停任务，再点击同步。后续自动接管仅切换模型请求，不会在后台反复重启并搬迁工作区。

若两个账号曾分别修改同一会话，保持当前消息与执行状态，将另一侧完整消息保存为 Science 原生历史分支。支持的分支格式会经过完整性校验；遇到未知格式或归档标识冲突仍中止并恢复，原始快照保留在恢复目录。

CC Switch 的 Claude Code 原生切号方式不能直接套用到 Science，见 [兼容实现对照](CC-SWITCH-COMPATIBILITY.md)。

---

# 0.8.28：指定优先消耗账号

0.8.28 曾使用下拉框和菜单；0.8.30 起改为每行账号旁的「优先消耗」开关。更改后自动开启接管，下一条请求开始优先使用所选账号；正在回复的请求保持原账号。

优先账号明确限频或失效时尝试其他账号，遵守官方 Retry-After；冷却结束后再次优先尝试指定账号。偏好独立持久化，不因备用账号成功回复而改变。临时「用于后续推理」仍是只使用该账号的手动模式；重新开启自动接管后恢复原优先偏好。

macOS 版内置匹配架构的 Python 3.13、cryptography 和 CA 根证书，启动时验证版本、模块与加密运算，管理功能无需修改系统 Python。下载 Apple Silicon 或 Intel 对应包，最低 macOS 11；Science 自身仍受其官方版本要求限制。

---

# 0.8.27：Science 免重启推理切换

macOS 已保存的两个账号可启用「免重启推理切换」。首次接入会重启 Science 一次，后续在账号的「… → 用于后续推理」选择账号，或开启自动接管。当前回复继续使用原账号，下一条请求才采用新选择。无需反复完成浏览器登录；官方撤销授权时仍须重新授权。

Science 原生登录、组织工作区与会话数据库保持原样，本机订阅网关只为模型请求选择授权账号。界面同时显示 Science 登录身份、下次优先账号和最近实际完成推理的账号，避免将路由选择误报为原生登录改变。网关不迁移消息、不改请求上下文，也不重放已经开始返回内容的请求。

自动接管仅在请求返回内容前遇到明确的 401、429、503、529 时尝试其他有效账号。网络结果不明或中断流直接报告失败。两个账号均不可用时如实报错，不保证消除官方额度限制。所有授权信息继续使用本机加密存储，当前登录的刷新由 Science 自身负责。

额度来自官方接口，显示 5 小时、每周及接口提供的模型专项额度。应用启动时查询一次，此后手动刷新，显示时间每 30 秒在本地更新；可单独刷新一个账号，限频期间遵守退避。失败保留旧读数并标记，未知额度不显示为 100%。

[官方多账号文档](https://code.claude.com/docs/en/authentication#log-in-with-multiple-accounts) 的 CLAUDE_CONFIG_DIR 方法为 Claude Code 启动不同配置目录，各目录登录和历史独立；不能直接等同于 Science 原生跨组织热切换。CC Switch 的倒计时、相对时间、额度条与账号列布局已改编复用，见 [第三方来源说明](THIRD_PARTY.md)。Science 适配层针对其原生组织检查实现。

重新打开 Science 请使用 CSSwitch 的「打开官方 Science」以保留路由接入。恢复官方直连会重启一次。Windows 继续使用下文的原生登录切换。

---

# Claude Science 账号切换与 Resume

CSSwitch Next 0.8.26 支持在 macOS 和 Windows 切换 Science 的真实登录账号。切换完成后核对 Science 返回的目标身份、OAuth 失效标记及官方 profile 接口，模型请求由 Science 直接发送到官方服务。账号管理独立于其他软件。

## 使用

1. 先在官方 Science 完成一次登录并打开原会话，再在「订阅账号」添加两个官方账号。
2. 点击目标账号「切换登录」。Science 窗口和后台服务会暂时退出；正在进行的对话将中断。
3. CSSwitch 备份当前登录及两侧完整组织会话目录，将两侧历史合入暂存副本，校验消息与数据库关联后交给目标账号。启动 Science 后通过 `/api/auth/status` 和官方 profile 接口核对账号、组织、邮箱及授权状态。
4. macOS 由原生 Science 窗口建立本机页面登录，等待认证接口就绪后才确认成功。如其他启动方式出现本机 `Sign in` 页面，点击以重新连接后台服务；它不是重新授权官方账号。
5. 打开原项目、原会话，点击 **Resume**。不会自动重跑用户的科研计算。

macOS 适配 Science **0.1.72**，Windows 适配 **0.1.71 / 0.1.72** 的组织目录、Fernet / v2 OAuth 凭据布局；切换前检查实际凭据，未知版本或格式拒绝写入。Windows 支持配置中的自定义数据和认证目录。macOS 仍限默认目录。0.8.25 起正常符号链接按链接本身迁移，不读取或复制链接目标；Windows junction、设备及其他特殊文件仍不迁移，数据库 / WAL 链接拒绝写入；根目录单数据库可读取会话，但原生切换仍要求组织目录布局。

## 官方额度（0.8.25 起）

每个账号展示官方 `/api/oauth/usage` 的 5 小时 / 一周剩余百分比、重置时间和上次成功更新时间；有模型专属周限额时额外显示。剩余百分比来自 `100 - utilization`，不是本机请求日志推算。应用启动时查询一次，之后仅手动刷新；后端保留缓存，手动刷新成功读数时绕过缓存，HTTP 429 仍按重试时间退避。后台网络查询不占用切换锁，慢请求不会禁用账号按钮。

查询失败保留上次读数并标记过期；接口未提供的窗口显示“暂无数据”。失效授权显示重新授权入口。非当前账号遇到 401 时，可在切换锁内刷新一次并加密保存到其独立登录槽；当前 Science 登录的令牌不由额度模块刷新，避免竞争。非当前账号被其他设备或服务撤销的授权需要重新添加。只有规范化的额度与时间跨 IPC，不返回访问令牌、刷新令牌或服务端错误正文。

实现参考 [CC Switch 官方订阅查询](https://github.com/farion1231/cc-switch/blob/main/src-tauri/src/services/subscription.rs) 的官方端点、OAuth beta 请求头和时间窗口解析；Science 使用自身加密登录槽，未使用 Claude Code 的配置目录。

## 删除账号（0.8.24 起）

在账号右侧点击「删除」，确认后移除 CSSwitch 账号记录和用于切换的登录缓存；再次使用需要重新授权。当前 Science 已登录或已配置的账号需先切换到其他账号，再删除；有未完成的切换时需先恢复。尚未配置到 Science 的最后一个账号可以删除，列表恢复为空。

删除不停止 Science、不取消官方订阅，也不删除项目、会话、历史切换备份或 Science 自身登录文件。历史备份可能仍包含当时的加密登录数据。删除旧推理网关当前选中的账号后，停止为后续请求选择该账号，不自动改用其他身份；已经发出的请求不强制中断。

## 数据与凭据

- 切换前停止 Science，合并 SQLite WAL，检查数据库，再保存独立快照（macOS 使用 APFS 克隆，Windows 使用保留元数据的复制）。项目、会话 ID、消息、工具结果及 Resume 状态完整保留，不使用会清理部分执行状态的普通导入器。
- 当前登录账号的有效刷新凭据只由 Science 管理。首次转交后 CSSwitch 从账号池移除该账号的访问/刷新令牌；后续切换在 Science 停止后保存最新的加密原生登录，包含刷新后的令牌。非当前账号由额度模块在切换锁内刷新独立登录槽。订阅网关不再参与推理。
- 每次跨组织切换都会携带完整会话。已有目标历史合入当前快照，保留会话 ID、消息、工具结果和较新的记录；消息逐条核验、数据库外键校验通过后才发布。发现同一会话索引的不同消息分支时停止并保留两侧快照，不跳过共享后假报成功。0.8.25 的“各账号历史分别保留”行为已撤销。
- 会话目录文件按原路径补齐；同路径不同内容以当前工作区为准，另一版本同时保存在暂存快照的 `.csswitch-merge-conflicts-*` 和原始备份中。不会跟随链接向工作区外写入。进程租约和目标账号专属秘密表不合入会话。
- macOS 备份位于 `~/.csswitch-next/subscriptions/native/backups/`；Windows 备份位于实际数据目录下的 `.csswitch-subscription-backups/`，使暂存、发布和回滚始终在同一磁盘。界面会显示恢复目录。切换日志仍在 `~/.csswitch-next/subscriptions/native/switch.json`。
- macOS 使用私有父目录 0700、凭据文件 0600；Windows 使用 NTFS 私有 ACL，CSSwitch 账户库的 AES 密钥额外使用当前 Windows 用户的 DPAPI 保护。不会改写 Science 的 `encryption.key.dpapi`。Windows 安装包自带加密依赖，无需配置 Python。
- 失败时尝试恢复原登录，保留失败现场。进程中断导致事务未完成时，订阅页提供「恢复原账号」。恢复会保留切换前后的快照。
- 不修改 Science 应用代码，不替换邮箱显示文本，也不伪造授权身份。使用用户已完成官方 OAuth 授权的真实凭据。

## Resume 的范围

Resume 可继续已有上下文。内存中的 Python/R 对象、当前本地进程、SSH/云端计算和账号专属云端资源，不保证能随账号迁移或从任意中断位置继续；这些取决于 Science 的恢复能力。没有自动重复执行工具来掩盖失败。

2026-11-08 已在 macOS Science 0.1.72 上实测：原测试会话停止并出现 Resume 后，切换另一个已授权 Pro 账号，Science 菜单显示目标邮箱；原会话 ID 和 Resume 保留。恢复后完成无工具调用的 1 至 100 累加任务，返回 **5050**。随后往返切换两个账号，测试会话消息摘要和已完成状态保持一致。此验证不包括 Python/R 计算进程或远程任务恢复。

2026-11-09 在本机 0.8.26 完成两个真实账号的往返验证：官方 profile 身份核验通过，两边原有 51,541 条消息逐条哈希核对保留，项目及内部 frame ID 全部包含在切换后的数据库。界面中创建的测试会话 `9600b5a2-fe81-4f31-b6c8-a78d3d4bf527` 在 help → Monroe → help 后仍能直接发送消息，两次均正确回复原标记 `MAPLE-583`；最终安装版自动进入目标账号项目页，无额外本机 Sign in 步骤。最终校验为 17 个项目、1340 条内部 frame 记录、51,566 条消息（含测试）。完整原始快照保留在切换备份目录中。

### 与 CC Switch 的接续方式区别

核对 CC Switch 提交 `cdca0984231021c4eb7e8978af8670cd29c6e415`：其 [Claude Code 官方账号切换](https://github.com/farion1231/cc-switch/blob/cdca0984231021c4eb7e8978af8670cd29c6e415/src-tauri/src/services/claude_account/snapshot/switch.rs) 在 Claude Code 的刷新及存储锁内移动默认登录凭据，写后读回验证，并保留恢复日志；[共享层](https://github.com/farion1231/cc-switch/blob/cdca0984231021c4eb7e8978af8670cd29c6e415/src-tauri/src/services/claude_account/shared.rs) 链接 projects/file-history/todos/plans 等目录。其 [4.0.21 发布说明](https://github.com/farion1231/cc-switch/blob/cdca0984231021c4eb7e8978af8670cd29c6e415/docs/release-notes/v4.0.21-zh.md) 明确说明运行中的 Claude Code 可以不用重启切号，但 Remote Control 会断开，下一条消息需要重建提示缓存。

Science 的真实身份与组织数据库在服务启动时绑定。本实现已验证的是自动重启后免再次授权、共享原历史和继续原对话；尚未实现或声称具备 Claude Code 的运行中热切换能力。

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

命令行入口：`science-subscriptions.py status`、`usage`、`switch ACCOUNT_ID`、`recover`、`delete ACCOUNT_ID`。不要同时手工修改原生登录文件。

## 旧版行为

0.8.16 及以前仅切换推理网关使用的订阅账号，Science 登录身份与邮箱保持不变。该行为并不等价于完整登录切换。0.8.17 的账号按钮改为真实登录切换；切换完成后禁用旧网关推理选择与自动接管。
