# CSSwitch Next Desktop

Tauri 2 + 原生 HTML/CSS/JavaScript。运行方式、依赖、协议范围与验证说明见 [项目 README](../README.md)。

```bash
npm ci
npm run prepare:assets
npm run dev
npm run build
npm run test:ui
```

`src-tauri/src/config.rs` 管理原子配置与密钥掩码；`lib.rs` 管理连接命令及共享 Science 生命周期；`science_shared.rs` 使用原 HOME/data-dir 和独立 auth-dir，保留历史归属并阻止免登录进程读写认证钥匙串；`oauth_forge.rs` 只写 CSSwitch 的本地认证；`proc.rs` 提供进程定位与回环探活。

代理模块在 `../proxy/`。打包时代理、启动脚本、许可证一起进入 App Resources。网关在每个请求开始时读取配置快照，因此配置更新无需重启网关。连接验证使用独立短进程，不改变活动连接。

`CSSWITCH_DATA_DIR` 可将测试数据指向临时目录；`CSSWITCH_REPO` 仅用于显式指定开发资源目录。正式 App 使用自身 Resources 中的脚本。
