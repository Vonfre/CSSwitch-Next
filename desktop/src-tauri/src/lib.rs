//! CSSwitch desktop lifecycle and connection commands.
//! Science uses the native HOME/data directory with CSSwitch-owned local authentication.
//! Provider credentials remain in the private config; the gateway snapshots routes per request.

mod config;
mod oauth_forge;
mod proc;
mod science_shared;
mod science_ui;
mod mcp;
mod updater;
mod workspace;

use std::path::{Path, PathBuf};
use std::io::Write;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use serde::Deserialize;
use serde_json::json;
use tauri::{Manager, State};

const SCIENCE_BIN: &str = "/Applications/Claude Science.app/Contents/Resources/bin/claude-science";

#[tauri::command]
async fn workspace_manage(app: tauri::AppHandle, operation: String, params: Option<serde_json::Value>) -> Result<serde_json::Value, String> {
    let root = asset_root(&app).ok_or("无法定位本地管理资源。")?;
    tauri::async_runtime::spawn_blocking(move || workspace::manage(&root, &operation, params.unwrap_or(json!({}))))
        .await.map_err(|_| "本地管理任务异常。".to_string())?
}

#[tauri::command]
async fn mcp_manage(app: tauri::AppHandle, operation: String, params: Option<serde_json::Value>) -> Result<serde_json::Value, String> {
    let root = asset_root(&app).ok_or("无法定位 MCP 管理资源。")?;
    tauri::async_runtime::spawn_blocking(move || {
        let _operation = RUNTIME_OPERATION.lock().unwrap_or_else(|e| e.into_inner());
        mcp::manage(&root, &operation, params.unwrap_or(json!({})))
    })
        .await.map_err(|_| "MCP 管理任务异常。".to_string())?
}

fn science_bin() -> PathBuf {
    science_shared::preferred_binary(science_shared::home().ok().as_deref(), Path::new(SCIENCE_BIN))
}

#[derive(Default)]
struct AppState {
    science_ui: Option<Child>,
    science_ui_port: u16,
    science_ui_upstream: u16,
    proxy: Option<Child>,
    proxy_port: u16,
    secret: String,
    provider: String,
    /// 当前代理进程所用 key 的非加密指纹（仅内存、绝不落盘/打印）。
    /// 换 key 后指纹变化 → 触发重启，避免复用带旧 key 的代理。
    key_fp: u64,
    sandbox: Option<Child>,
    sandbox_port: u16,
    sandbox_url: Option<String>,
}

/// key 的非加密指纹（SipHash），只用于判断「key 是否变了」。绝不打印、绝不落盘。
fn key_fingerprint(s: &str) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut h = std::collections::hash_map::DefaultHasher::new();
    s.hash(&mut h);
    h.finish()
}

// ---------- provider 元信息 ----------
fn key_env(provider: &str) -> &'static str {
    match provider {
        "qwen" => "DASHSCOPE_API_KEY",
        _ => "DEEPSEEK_API_KEY",
    }
}

// ---------- 路径与日志 ----------
/// 定位 CSSwitch 仓库根（含 proxy/csswitch_proxy.py）。优先 CSSWITCH_REPO，
/// 否则从可执行文件与当前目录逐级上溯。找不到返回 None。
fn repo_root() -> Option<PathBuf> {
    let marker = Path::new("proxy/csswitch_proxy.py");
    // 显式指定优先：规范化后再判定，避免相对/软链歧义。
    if let Some(r) = std::env::var_os("CSSWITCH_REPO") {
        if let Ok(p) = std::fs::canonicalize(PathBuf::from(r)) {
            if p.join(marker).is_file() {
                return Some(p);
            }
        }
    }
    // 否则只从【可执行文件位置】上溯。刻意不看 current_dir：启动目录可被影响，
    // 若据此找到别处的 csswitch_proxy.py，会把带 key 的环境交给来路不明的脚本。
    if let Ok(exe) = std::env::current_exe() {
        let mut dir: Option<&Path> = exe.parent();
        while let Some(d) = dir {
            if d.join(marker).is_file() {
                return Some(d.to_path_buf());
            }
            dir = d.parent();
        }
    }
    None
}

/// 定位「资源根」（含 proxy/、scripts/）。打包成 .app 后，proxy/ 与 scripts/ 被
/// bundle 进 `Contents/Resources`；开发态则回退到仓库根。找不到返回 None。
/// 这样从 Finder 启动的正式 .app 也能找到代理脚本（修 P1-1）。
fn asset_root(app: &tauri::AppHandle) -> Option<PathBuf> {
    let marker = Path::new("proxy/csswitch_proxy.py");
    // 打包态：Tauri 资源目录。
    if let Ok(res) = app.path().resource_dir() {
        if res.join(marker).is_file() {
            return Some(res);
        }
    }
    // 开发态：从可执行文件位置上溯（见 repo_root 注释，刻意不看 current_dir）。
    repo_root()
}

/// Retained legacy isolated data, for diagnostics only. Never migrated or deleted.
fn sandbox_home() -> PathBuf {
    config::default_dir().join("sandbox").join("home")
}

fn shared_root() -> PathBuf {
    config::default_dir().join("shared-science")
}

fn log_path(name: &str) -> PathBuf {
    config::default_dir().join("logs").join(name)
}

/// `O_NOFOLLOW` 的平台常量（本项目不引 libc）。macOS/BSD=0x0100，Linux=0x20000。
const fn libc_o_nofollow() -> i32 {
    if cfg!(target_os = "linux") {
        0x2_0000
    } else {
        0x0100
    }
}

/// 打开（truncate）一个子进程日志文件，父目录 0700、文件 0600（防同机其它用户读到 secret 尾巴）。
fn open_log(name: &str) -> std::io::Result<std::fs::File> {
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    let p = log_path(name);
    if let Some(parent) = p.parent() {
        config::assert_not_symlink(parent)?;
        std::fs::create_dir_all(parent)?;
        let _ = std::fs::set_permissions(parent, std::fs::Permissions::from_mode(0o700));
    }
    // 日志路径不许是符号链接：否则 truncate+写会覆盖链接目标文件（修 P2-1）。
    config::assert_not_symlink(&p)?;
    let f = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        // O_NOFOLLOW：即便在 lstat 与 open 之间被换成软链，也拒绝跟随。
        .custom_flags(libc_o_nofollow())
        .open(&p)?;
    // 文件已存在时 mode() 不复位，显式再夹一次。
    let _ = std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o600));
    Ok(f)
}

/// 把字符串里的 secret 明文替换成 ****，用于任何要回显给前端的错误尾巴。
fn redact(s: &str, secret: &str) -> String {
    if secret.is_empty() {
        s.to_string()
    } else {
        s.replace(secret, "****")
    }
}

fn tail_file(path: &Path, max: usize) -> String {
    match std::fs::read(path) {
        Ok(b) => {
            let start = b.len().saturating_sub(max);
            String::from_utf8_lossy(&b[start..]).trim().to_string()
        }
        Err(_) => String::new(),
    }
}

fn kill_child(slot: &mut Option<Child>) {
    if let Some(mut c) = slot.take() {
        let _ = c.kill();
        let _ = c.wait();
    }
}

/// 取锁并从 poison 中恢复：某线程持锁时 panic 不应把整个 app 卡死。
fn lock(m: &Mutex<AppState>) -> std::sync::MutexGuard<'_, AppState> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

/// 用系统浏览器打开 URL（macOS `open`）。校验退出码：非零视为失败（P2c）。
fn open_in_browser(url: &str) -> Result<(), String> {
    let st = Command::new("open")
        .arg(url)
        .status()
        .map_err(|e| format!("打开浏览器失败：{e}"))?;
    if !st.success() {
        return Err(format!("open 非零退出（{:?}）", st.code()));
    }
    Ok(())
}

// ---------- 代理生命周期核心 ----------
/// 转义 ERE（extended regex）元字符，让路径按字面参与 `pkill -f` 匹配（避免路径里的
/// `.`/`(`/`[` 等被当作正则、误配或失配）。
fn ere_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 8);
    for c in s.chars() {
        if "\\.^$*+?()[]{}|".contains(c) {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

/// 本次 ensure_proxy 对代理做了什么（供一键据实提示）。
#[derive(Clone, Copy, PartialEq)]
enum ProxyAction {
    Reused,    // 端口+provider+key 指纹一致且健康，原样复用
    Restarted, // 首次起 / 换 key / 换 provider / 不健康，重起了代理
}

/// 确保代理在跑且健康；返回 (端口, secret, 本次动作)。幂等：已健康则复用。
fn ensure_proxy(
    app: &tauri::AppHandle,
    state: &State<'_, Mutex<AppState>>,
) -> Result<(u16, String, ProxyAction), String> {
    let dir = config::default_dir();
    let cfg = config::load_from(&dir).map_err(|e| e.to_string())?;
    if cfg.mode == "official" {
        return Err("请先切换到第三方模型模式。".into());
    }
    let provider = cfg.provider.clone();
    let connection = cfg.connection_for(&provider).ok_or("当前连接不存在。")?;
    connection.validate()?;
    let key = cfg
        .key_for(&provider)
        .ok_or_else(|| format!("缺少 {provider} 的 API key，请先在面板填写并保存。"))?;
    let key_fp = key_fingerprint(&key);
    let port = cfg.proxy_port;
    let root = asset_root(app)
        .ok_or("找不到代理脚本 proxy/csswitch_proxy.py（打包资源或仓库根均未命中）。开发态可设 CSSWITCH_REPO。")?;
    let py = proc::find_exe("python3")
        .ok_or("缺少依赖 python3（起翻译代理需要）。已查 PATH、常见目录与登录 shell 仍未找到；macOS 一般自带 /usr/bin/python3（装 Xcode 命令行工具：xcode-select --install）。")?;

    // path-secret：**持久化复用**。已在跑的沙箱把该 secret 嵌进了 ANTHROPIC_BASE_URL，
    // 若每次起代理都换 secret，代理一重启（换 key/换 provider/重开 app）沙箱就会拿旧 secret
    // 打到新代理 → 全部 403（修 P1：代理重启后沙箱失联）。故从 config 读稳定 secret，
    // 首次为空才生成一次并写回，之后所有代理进程都复用它。
    let secret = if !cfg.secret.is_empty() {
        cfg.secret.clone()
    } else {
        let s = proc::gen_secret().map_err(|e| format!("无法生成安全 secret：{e}"))?;
        let s2 = s.clone();
        config::update(&dir, move |c| c.secret = s2).map_err(|e| e.to_string())?;
        s
    };

    // 整个「检查 → 清残留 → 起进程 → 记账」在同一把锁内完成，避免并发双击时
    // 两路都判定「没健康代理」各起一个、后者覆盖前者的 Child 句柄导致前者被孤儿泄漏。
    {
        let mut st = lock(state);
        // The gateway snapshots the selected connection from the atomic config file per request.
        if st.proxy.is_some()
            && st.proxy_port == port
            && proc::http_health(port, Some(&st.secret), 500)
        {
            return Ok((port, st.secret.clone(), ProxyAction::Reused));
        }
        // 清残留（换端口/换 provider/换 key/不健康）。
        kill_child(&mut st.proxy);
        let script = root.join("proxy/csswitch_proxy.py");
        // 再清掉上次会话遗留、绑在同端口上的孤儿代理：崩溃或强退不会触发本进程的 kill，
        // 孤儿仍占着端口 → 新代理绑不上（Errno 48）→ 探活超时。
        // 收紧（P2 GPT 复审）：匹配【本安装的绝对脚本路径】+ 端口，而非仅「脚本名+端口」，
        // 避免误杀另一个 checkout / 用户手启的同名代理。路径里的正则元字符转义按字面匹配。
        let pat = format!("{}.*--port {port}", ere_escape(&script.to_string_lossy()));
        let _ = Command::new("pkill").arg("-f").arg(&pat).status();

        let logf = open_log("proxy.log").map_err(|e| format!("建日志失败：{e}"))?;
        let logf2 = logf.try_clone().map_err(|e| e.to_string())?;
        let child = Command::new(&py)
            .arg("-B")
            .arg(&script)
            .arg("--provider")
            .arg(&provider)
            .arg("--port")
            .arg(port.to_string())
            .arg("--config")
            .arg(dir.join("config.json"))
            .arg("--usage-file")
            .arg(dir.join("usage.jsonl"))
            // key 经环境变量注入，绝不作为命令行参数（避免 ps 泄露）。
            .env(key_env(&provider), &key)
            .env("CSSWITCH_AUTH_TOKEN", &secret)
            .stdout(Stdio::from(logf))
            .stderr(Stdio::from(logf2))
            .spawn()
            .map_err(|e| format!("启动代理失败：{e}"))?;
        st.proxy = Some(child);
        st.proxy_port = port;
        st.secret = secret.clone();
        st.provider = provider;
        st.key_fp = key_fp;
    }

    // 探活最多 ~4s（锁外，不阻塞 status 等命令）。
    let mut ok = false;
    for _ in 0..40 {
        std::thread::sleep(Duration::from_millis(100));
        if proc::http_health(port, Some(&secret), 400) {
            ok = true;
            break;
        }
    }
    if !ok {
        let mut st = lock(state);
        // 只在仍是本次起的代理时才清（secret 匹配），避免误杀并发重启起来的新代理。
        if st.secret == secret {
            kill_child(&mut st.proxy);
        }
        let tail = redact(&tail_file(&log_path("proxy.log"), 500), &secret);
        return Err(format!(
            "代理起后探活超时（端口 {port} 可能被占用，或 key 无效）。\n{tail}"
        ));
    }
    Ok((port, secret, ProxyAction::Restarted))
}

/// Stop only the shared daemon launched with CSSwitch's private config.
fn stop_sandbox_inner(_app: &tauri::AppHandle, st: &mut AppState) -> Result<(), String> {
    science_shared::stop_owned(&science_bin(), &shared_root())?;
    kill_child(&mut st.sandbox);
    kill_child(&mut st.science_ui);
    st.sandbox_url = None;
    st.sandbox_port = 0;
    Ok(())
}

// ---------- Tauri commands ----------
#[tauri::command]
fn get_config() -> Result<serde_json::Value, String> {
    let dir = config::default_dir();
    let cfg = config::load_from(&dir).map_err(|e| e.to_string())?;
    Ok(public_config(&cfg))
}

fn public_config(cfg: &config::Config) -> serde_json::Value {
    let ids: Vec<String> = cfg.providers.keys().cloned().collect();
    let connections: Vec<_> = ids.iter().filter_map(|id| cfg.connection_for(id).map(|p| json!({
        "id": id, "name": p.name, "protocol": p.protocol, "base_url": p.base_url,
        "model": p.model, "quality_model": p.quality_model, "fast_model": p.fast_model,
        "max_tokens": p.max_tokens, "key_masked": config::mask(&p.key), "has_key": !p.key.is_empty()
    }))).collect();
    json!({
        "provider": if cfg.connection_for(&cfg.provider).is_some() { cfg.provider.as_str() } else { "" },
        "proxy_port": cfg.proxy_port,
        "sandbox_port": cfg.sandbox_port,
        "mode": cfg.mode,
        "science_local_mcp": cfg.science_local_mcp,
        "science_zh": cfg.science_zh,
        "connections": connections,
    })
}

/// 切换运行模式（"proxy" 第三方 / "official" 官方）。
///
/// 切到「官方」是**真正的切换**，不只是改配置：先把第三方链路拆掉（停沙箱 Science + 杀代理、
/// 清 secret）。否则代理/沙箱会留在后台空跑；且 macOS 单实例语义下，后面 `open` 可能只是聚焦
/// 还活着的沙箱实例（带着改过的 ANTHROPIC_* 环境）而非官方实例，把用户误导回第三方链路。
/// 切回「第三方」不自动启动服务；启动后使用共享数据与独立本地认证。
fn set_mode_inner(
    app: tauri::AppHandle,
    state: State<'_, Mutex<AppState>>,
    mode: String,
) -> Result<(), String> {
    if mode != "proxy" && mode != "official" {
        return Err(format!("未知模式：{mode}（只支持 proxy / official）。"));
    }
    let dir = config::default_dir();

    // 事务化（修 P2 GPT 复审）：切官方要「先拆第三方链路，成功了再落盘 official」。
    // 旧序（先落盘再拆）若拆沙箱失败，会留下「磁盘=official、UI/进程=第三方」的状态分裂
    // （前端收到 Err 保持第三方 UI，磁盘却已是 official，下次启动就错进官方模式）。
    // 现序保证：拆失败 → 不落盘、保持 proxy 模式、如实报错，磁盘/UI/进程一致。
    if mode == "official" {
        {
            let mut st = lock(&state);
            // 先停沙箱：失败就在动代理/落盘之前中止，状态不分裂。
            stop_sandbox_inner(&app, &mut st)
                .map_err(|e| format!("停止共享 Science 失败，未切换到官方模式：{e}"))?;
            kill_child(&mut st.proxy);
            st.secret.clear();
        }
    }
    // 拆链已成功（或切回 proxy 无需拆）→ 落盘。
    config::update(&dir, {
        let mode = mode.clone();
        move |c| c.mode = mode
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// 官方模式：干净地打开用户【真实】的 Claude Science（用户自己的官方登录与订阅）。
///
/// 铁律：绝不碰/复制真实凭证；用 `open`（系统 LaunchServices 正常启动）而非注入环境变量，
/// 并显式抹掉任何 `ANTHROPIC_*`，确保**不用改过的环境变量启动真实实例**（真实实例走它自己的
/// 官方端点，不经本代理）。CSSwitch 只把用户交回官方客户端，不托管其登录。
#[tauri::command]
fn open_official() -> Result<(), String> {
    let app_path = "/Applications/Claude Science.app";
    let mut cmd = Command::new("open");
    if Path::new(app_path).is_dir() {
        cmd.arg(app_path);
    } else {
        cmd.arg("-a").arg("Claude Science");
    }
    // 防御性：即便 `open` 通常不向被启动 app 传本进程环境，也显式抹掉，杜绝把改过的
    // ANTHROPIC_* 带进真实实例（铁律 3）。
    cmd.env_remove("ANTHROPIC_BASE_URL")
        .env_remove("ANTHROPIC_API_KEY")
        .env_remove("ANTHROPIC_AUTH_TOKEN");
    match cmd.status() {
        Ok(s) if s.success() => Ok(()),
        Ok(_) => Err("未能打开 Claude Science。请确认已安装官方 Claude Science。".into()),
        Err(e) => Err(format!("打开官方 Claude Science 失败：{e}")),
    }
}

#[derive(Deserialize)]
struct UiSettings {
    provider: String,
    proxy_port: u16,
    sandbox_port: u16,
}

#[tauri::command]
fn set_config(cfg: UiSettings, state: State<'_, Mutex<AppState>>) -> Result<(), String> {
    // The inference gateway must not occupy the native Science port.
    if cfg.proxy_port == 8765 {
        return Err("网关不能使用 Science 保留端口 8765。".into());
    }
    // 只认已实现的 provider，避免存进未知值后起代理时才失败（修 P2-3）。
    let current = config::load_from(&config::default_dir()).map_err(|e| e.to_string())?;
    if !cfg.provider.is_empty() && current.connection_for(&cfg.provider).is_none() {
        return Err("连接不存在。".into());
    }
    if (current.proxy_port != cfg.proxy_port || current.sandbox_port != cfg.sandbox_port)
        && (lock(&state).proxy.is_some() || sandbox_running_ours(current.sandbox_port))
    {
        return Err("请先停止服务，再修改端口。".into());
    }
    // 端口 0 非法（无法监听/探活）。
    if cfg.proxy_port == 0 || cfg.sandbox_port == 0 {
        return Err("端口不能为 0。".into());
    }
    // 代理与沙箱不能同端口，否则互相抢占。
    if cfg.proxy_port == cfg.sandbox_port {
        return Err("代理端口与 Science 端口不能相同。".into());
    }
    let dir = config::default_dir();
    config::update(&dir, move |c| {
        c.provider = cfg.provider;
        c.proxy_port = cfg.proxy_port;
        c.sandbox_port = cfg.sandbox_port;
    })
    .map(|_| ())
    .map_err(|e| e.to_string())
}

#[derive(Deserialize)]
struct ConnectionInput {
    id: String,
    name: String,
    protocol: String,
    base_url: String,
    model: String,
    quality_model: String,
    fast_model: String,
    max_tokens: u32,
    key: Option<String>,
}

#[tauri::command]
fn save_connection(input: ConnectionInput) -> Result<(), String> {
    if input.id.is_empty()
        || input.id.len() > 80
        || !input
            .id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return Err("连接 ID 无效。".into());
    }
    let mut p = config::ProviderCfg {
        name: input.name.trim().into(),
        protocol: input.protocol,
        base_url: input.base_url.trim().trim_end_matches('/').into(),
        model: input.model.trim().into(),
        quality_model: input.quality_model.trim().into(),
        fast_model: input.fast_model.trim().into(),
        max_tokens: input.max_tokens,
        ..Default::default()
    };
    p.validate()?;
    config::update(&config::default_dir(), move |c| {
        p.key = input
            .key
            .filter(|s| !s.trim().is_empty())
            .map(|s| s.trim().into())
            .unwrap_or_else(|| {
                c.connection_for(&input.id)
                    .map(|p| p.key)
                    .unwrap_or_default()
            });
        c.providers.insert(input.id, p);
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn select_connection(id: String, state: State<'_, Mutex<AppState>>) -> Result<(), String> {
    let cfg = config::load_from(&config::default_dir()).map_err(|e| e.to_string())?;
    let p = cfg.connection_for(&id).ok_or("连接不存在。")?;
    p.validate()?;
    if lock(&state).proxy.is_some() && p.key.is_empty() {
        return Err("请先为该连接保存 API Key。".into());
    }
    config::update(&config::default_dir(), move |c| c.provider = id).map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn delete_connection(id: String, state: State<'_, Mutex<AppState>>) -> Result<(), String> {
    let st = lock(&state);
    config::update_checked(&config::default_dir(), |c| c.remove_connection(&id, st.proxy.is_some()))
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn clear_connection_key(id: String, state: State<'_, Mutex<AppState>>) -> Result<(), String> {
    let cfg = config::load_from(&config::default_dir()).map_err(|e| e.to_string())?;
    if cfg.provider == id && lock(&state).proxy.is_some() {
        return Err("请先停止服务，再清除当前连接密钥。".into());
    }
    config::update(&config::default_dir(), move |c| {
        if let Some(p) = c.providers.get_mut(&id) {
            p.key.clear();
        }
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
async fn verify_connection(app: tauri::AppHandle, id: String) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let cfg = config::load_from(&config::default_dir()).map_err(|e| e.to_string())?;
        if cfg.connection_for(&id).is_none() {
            return Err("连接不存在。".into());
        }
        let root = asset_root(&app).ok_or("找不到代理资源。")?;
        let py = proc::find_exe("python3").ok_or("未找到 python3。")?;
        let out = Command::new(py)
            .arg("-B")
            .arg(root.join("proxy/csswitch_proxy.py"))
            .arg("--probe")
            .arg("--provider")
            .arg(id)
            .arg("--config")
            .arg(config::default_dir().join("config.json"))
            .output()
            .map_err(|e| e.to_string())?;
        serde_json::from_slice(&out.stdout).map_err(|_| "连接验证未返回有效结果。".into())
    })
    .await
    .map_err(|e| e.to_string())?
}

fn probe_profile(input: ConnectionInput, cfg: &config::Config) -> Result<config::ProviderCfg, String> {
    let mut profile = catalog_profile(ModelQuery {
        id: input.id,
        protocol: input.protocol,
        base_url: input.base_url,
        key: input.key,
    }, cfg)?;
    profile.name = input.name.trim().into();
    profile.model = input.model.trim().into();
    profile.quality_model = input.quality_model.trim().into();
    profile.fast_model = input.fast_model.trim().into();
    profile.max_tokens = input.max_tokens;
    profile.validate()?;
    Ok(profile)
}

#[tauri::command]
async fn test_connection(app: tauri::AppHandle, input: ConnectionInput) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let cfg = config::load_from(&config::default_dir()).map_err(|e| e.to_string())?;
        let profile = probe_profile(input, &cfg)?;
        let root = asset_root(&app).ok_or("找不到代理资源。")?;
        let py = proc::find_exe("python3").ok_or("未找到 python3。")?;
        // Unsaved credentials stay in stdin, never in a temporary config or argv.
        let mut child = Command::new(py).arg("-B")
            .arg(root.join("proxy/csswitch_proxy.py")).arg("--probe-stdin")
            .env_remove("CSSWITCH_UPSTREAM_URL")
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
            .spawn().map_err(|_| "无法启动连接测试。")?;
        let payload = serde_json::to_vec(&json!({"provider": "draft", "providers": {"draft": profile}}))
            .map_err(|_| "无法生成测试配置。")?;
        if child.stdin.take().ok_or("无法写入测试配置。")?.write_all(&payload).is_err() {
            let _ = child.kill();
            let _ = child.wait();
            return Err("无法写入测试配置。".into());
        }
        let out = child.wait_with_output().map_err(|_| "连接测试失败。")?;
        serde_json::from_slice(&out.stdout).map_err(|_| "连接测试未返回有效结果。".into())
    }).await.map_err(|e| e.to_string())?
}

#[derive(Deserialize)]
struct ModelQuery {
    id: String,
    protocol: String,
    base_url: String,
    key: Option<String>,
}

fn catalog_profile(query: ModelQuery, cfg: &config::Config) -> Result<config::ProviderCfg, String> {
    let mut profile = config::ProviderCfg {
        name: "catalog".into(),
        model: "catalog".into(),
        protocol: query.protocol,
        base_url: query.base_url.trim().trim_end_matches('/').into(),
        max_tokens: 1,
        ..Default::default()
    };
    profile.validate()?;
    profile.key = match query.key.filter(|k| !k.trim().is_empty()) {
        Some(key) => key.trim().into(),
        None => {
            let saved = cfg.connection_for(&query.id).ok_or("请填写 API Key。")?;
            if saved.base_url.trim_end_matches('/') != profile.base_url || saved.protocol != profile.protocol {
                return Err("API 地址或协议已改变，请重新填写密钥后获取模型，或先保存连接。".into());
            }
            saved.key
        }
    };
    if profile.key.is_empty() || profile.key.len() > 16384 || profile.key.contains(['\r', '\n']) {
        return Err("请填写有效 API Key。".into());
    }
    Ok(profile)
}

#[tauri::command]
async fn list_models(app: tauri::AppHandle, query: ModelQuery) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let cfg = config::load_from(&config::default_dir()).map_err(|e| e.to_string())?;
        let profile = catalog_profile(query, &cfg)?;
        let root = asset_root(&app).ok_or("找不到模型列表资源。")?;
        let py = proc::find_exe("python3").ok_or("未找到 python3。")?;
        let mut child = Command::new(py).arg("-B").arg(root.join("proxy/model_catalog.py"))
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
            .spawn().map_err(|_| "无法启动模型列表查询。")?;
        let payload = serde_json::to_vec(&json!({"base_url": profile.base_url, "protocol": profile.protocol, "key": profile.key})).map_err(|_| "无法生成模型查询。")?;
        let written = child.stdin.take().ok_or("无法写入模型查询。")?.write_all(&payload);
        if written.is_err() {
            let _ = child.kill();
            let _ = child.wait();
            return Err("无法写入模型查询。".into());
        }
        let out = child.wait_with_output().map_err(|_| "模型列表查询失败。")?;
        serde_json::from_slice(&out.stdout).map_err(|_| "模型列表未返回有效结果。".into())
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn read_usage() -> Result<serde_json::Value, String> {
    let path = config::default_dir().join("usage.jsonl");
    config::assert_not_symlink(&path).map_err(|e| e.to_string())?;
    let data = match std::fs::read_to_string(path) {
        Ok(data) => data,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(json!({"entries": []})),
        Err(e) => return Err(e.to_string()),
    };
    let entries: Vec<serde_json::Value> = data
        .lines()
        .rev()
        .take(2000)
        .filter_map(|s| serde_json::from_str(s).ok())
        .collect();
    Ok(json!({"entries": entries}))
}

#[tauri::command]
fn read_logs() -> Result<serde_json::Value, String> {
    let cfg = config::load_from(&config::default_dir()).map_err(|e| e.to_string())?;
    let clean = |name| {
        let mut s = redact(&tail_file(&log_path(name), 24000), &cfg.secret);
        for p in cfg.providers.values() {
            if !p.key.is_empty() {
                s = s.replace(&p.key, "[REDACTED]");
            }
        }
        s
    };
    Ok(json!({"proxy": clean("proxy.log"), "sandbox": clean("sandbox.log")}))
}

#[tauri::command]
async fn diagnostics() -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let data = science_shared::home().and_then(|h| science_shared::local_settings(&h).map(|s| s.0));
        let history = data.as_ref().ok().and_then(|dir| science_shared::history_summary(dir).ok());
        json!({
            "science_installed": science_bin().is_file(),
            "python": proc::find_exe("python3").map(|p| p.to_string_lossy().to_string()),
            "config_dir": config::default_dir().to_string_lossy(),
            "science_data_dir": science_shared::home().and_then(|h| science_shared::local_settings(&h).map(|s| s.0)).map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|e| e),
            "science_auth_dir": shared_root().join("auth").to_string_lossy(),
            "legacy_sandbox_home": sandbox_home().to_string_lossy(),
            "science_history": history,
            "version": env!("CARGO_PKG_VERSION"),
            "platform": std::env::consts::ARCH
        })
    })
    .await
    .map_err(|e| e.to_string())
}

#[tauri::command]
fn save_provider_key(provider: String, key: String) -> Result<String, String> {
    let dir = config::default_dir();
    let key2 = key.clone();
    config::update(&dir, move |c| {
        c.providers.entry(provider).or_default().key = key2;
    })
    .map_err(|e| e.to_string())?;
    Ok(config::mask(&key))
}

#[tauri::command]
fn start_proxy(
    app: tauri::AppHandle,
    state: State<'_, Mutex<AppState>>,
) -> Result<serde_json::Value, String> {
    let (port, _secret, _action) = ensure_proxy(&app, &state)?;
    Ok(json!({ "port": port }))
}

/// 「存 key 即验证」：确保代理在跑，再经代理向上游发一个**最小**请求
/// （`max_tokens:1`，一句 "ping"），据响应状态码判断 key 是否真的可用。
/// 返回 `{ok, hint}`：ok=true 表示上游接受（key 有效）；ok=false 表示上游拒绝或异常，
/// hint 给人话。彻底避免「只看绿灯（代理起来了）≠ key 真能用」。
#[tauri::command]
fn verify_key(
    app: tauri::AppHandle,
    state: State<'_, Mutex<AppState>>,
) -> Result<serde_json::Value, String> {
    let (port, secret, _action) = ensure_proxy(&app, &state)?;
    // 走稳定模型 id（代理内部映射到当前 provider 的真实模型），非流式、只要 1 个 token。
    let body = br#"{"model":"claude-opus-4-8","max_tokens":1,"messages":[{"role":"user","content":"ping"}]}"#;
    match proc::http_post_status(port, Some(&secret), "/v1/messages", body, 15000) {
        Some(200) => Ok(json!({ "ok": true, "hint": "key 有效，上游已接受。" })),
        Some(code @ (401 | 403)) => Ok(
            json!({ "ok": false, "hint": format!("上游拒绝（{code}），key 可能无效或无权限。") }),
        ),
        Some(code) => Ok(json!({
            "ok": false,
            "hint": format!("上游返回 {code}，可能是 key 无效、额度不足或上游异常。")
        })),
        None => Err("验证请求无响应（多为网络或上游不通）。".to_string()),
    }
}

fn stop_all_inner(app: tauri::AppHandle, state: State<'_, Mutex<AppState>>) -> Result<(), String> {
    let mut st = lock(&state);
    // 先停沙箱并记录结果；代理无论如何都杀。沙箱没停干净则如实返错，不虚报成功。
    let sandbox_res = stop_sandbox_inner(&app, &mut st);
    kill_child(&mut st.proxy);
    st.secret.clear();
    st.proxy_port = 0;
    st.sandbox_port = 0;
    sandbox_res.map_err(|e| format!("代理已停；但共享 Science 未能停止：{e}"))
}

fn launch_inner(
    app: tauri::AppHandle,
    state: State<'_, Mutex<AppState>>,
) -> Result<serde_json::Value, String> {
    let cfg = config::load_from(&config::default_dir()).map_err(|e| e.to_string())?;
    if cfg.mode != "proxy" {
        return Err("请先切换到第三方模式。".into());
    }
    let (pport, secret, _) = ensure_proxy(&app, &state)?;
    let sport = cfg.sandbox_port;
    let binary = science_bin();
    let bin = binary.as_path();
    if !bin.is_file() {
        return Err("请先安装 Claude Science。".into());
    }
    if !sandbox_running_ours(sport) {
        let root = shared_root();
        science_shared::stop_owned(bin, &root)?;
        let home = science_shared::home()?;
        let session = science_shared::prepare(&home, &root, sport)?;
        science_shared::apply_mcp_policy(&session, cfg.science_local_mcp)?;
        science_shared::ensure_port_available(bin, &session)?;
        // A data directory has one daemon. Stop the original before changing its inference route.
        science_shared::stop(bin, &session)?;
        science_shared::save(&root, &session)?;
        let logf = open_log("sandbox.log").map_err(|e| e.to_string())?;
        let status = science_shared::command(bin, &session, "serve")
            .args([
                "--port",
                &sport.to_string(),
                "--no-browser",
                "--detached",
            ])
            .env(
                "ANTHROPIC_BASE_URL",
                format!("http://127.0.0.1:{pport}/{secret}"),
            )
            .env("https_proxy", format!("http://127.0.0.1:{pport}"))
            .env("HTTPS_PROXY", format!("http://127.0.0.1:{pport}"))
            .env("no_proxy", "127.0.0.1,localhost,::1")
            .env("NO_PROXY", "127.0.0.1,localhost,::1")
            .env("OPERON_DISABLE_NONESSENTIAL_TRAFFIC", "1")
            .stdout(Stdio::from(logf.try_clone().map_err(|e| e.to_string())?))
            .stderr(Stdio::from(logf))
            .status()
            .map_err(|e| e.to_string())?;
        let ready = status.success()
            && (0..100).any(|_| {
                std::thread::sleep(Duration::from_millis(100));
                sandbox_running_ours(sport)
            });
        if !ready {
            let cleanup = science_shared::stop_owned(bin, &root);
            let tail = redact(&tail_file(&log_path("sandbox.log"), 600), &secret);
            return Err(format!("共享 Science 启动失败。请关闭原来的 Science 窗口后重试。原有凭据和历史未被替换。清理结果：{cleanup:?}\n{tail}"));
        }
    }
    let mcp_notice = if cfg.science_local_mcp {
        match asset_root(&app).and_then(|root| mcp::manage(&root, "install", json!({})).ok()) {
            Some(result) => result.get("message").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            None => "本机连接器接入未完成，请在 MCP 管理中查看并重试。".into(),
        }
    } else { String::new() };
    let native_url = sandbox_url(sport);
    let url = science_ui::entry(&app, &mut lock(&state), sport, &native_url)?;
    {
        let mut st = lock(&state);
        st.sandbox_port = sport;
        st.sandbox_url = Some(url.clone());
    }
    let msg = match open_in_browser(&url) {
        Ok(()) => "已打开 Science，使用本机原有对话记录，第三方模式免登录。".to_string(),
        Err(_) => format!("Science 已就绪，请手动打开：{url}"),
    };
    Ok(json!({ "url": url, "msg": msg, "action": "opened", "mcp_notice": mcp_notice }))
}

/// 从 `claude-science url` 的 stdout 里取**第一条**合法 http(s) URL。
/// Science 的 `url` 命令会输出多行（第一行是真 URL，随后行是「single-use…」说明）；把整段
/// stdout 当 URL 交给 `open` 会带上换行与说明文字 → 打开错误入口、nonce 不被正确消费 → 落到
/// `/login`（修 0.2.1 Bug1）。故逐行找第一条以 `http://`/`https://` 开头的行，并只取该行首个
/// 非空白 token（URL 内不含空白，若同行尾随了说明也被切掉）。找不到返回 None。
fn first_http_url(stdout: &str) -> Option<String> {
    for line in stdout.lines() {
        let t = line.trim();
        if t.starts_with("http://") || t.starts_with("https://") {
            let url = t.split_whitespace().next().unwrap_or(t);
            return Some(url.to_string());
        }
    }
    None
}

/// Mint a single-use local UI link using the same data/config identity as launch.
fn sandbox_url(port: u16) -> String {
    if let Ok(Some(session)) = science_shared::load(&shared_root()) {
        if let Ok(out) = science_shared::command(&science_bin(), &session, "url").output() {
            if out.status.success() {
                if let Some(url) = first_http_url(&String::from_utf8_lossy(&out.stdout)) {
                    return url;
                }
            }
        }
    }
    format!("http://127.0.0.1:{port}")
}

fn sandbox_running_ours(port: u16) -> bool {
    let Ok(Some(session)) = science_shared::load(&shared_root()) else {
        return false;
    };
    if session.port != port {
        return false;
    }
    let Ok(status) = science_shared::status(&science_bin(), &session) else {
        return false;
    };
    science_shared::owns_status(&status, &session) && proc::http_health(port, None, 400)
}

fn status_inner(state: State<'_, Mutex<AppState>>) -> serde_json::Value {
    // 只在锁内取值，锁外做阻塞探活。
    let (pport, secret, sport, provider) = {
        let st = lock(&state);
        let cfg = config::load_from(&config::default_dir()).unwrap_or_default();
        let pport = if st.proxy_port != 0 {
            st.proxy_port
        } else {
            cfg.proxy_port
        };
        let sport = if st.sandbox_port != 0 {
            st.sandbox_port
        } else {
            cfg.sandbox_port
        };
        (pport, st.secret.clone(), sport, cfg.provider)
    };
    let proxy = if !secret.is_empty() && proc::http_health(pport, Some(&secret), 300) {
        "green"
    } else {
        "amber"
    };
    // 状态灯也用 data-dir 强身份（修 P2 GPT 复审），避免端口被冒名服务占用时误显绿灯。
    // status() 是按需调用（前端 refreshStatus 在动作后触发，非高频轮询），一次子进程可接受。
    let sandbox = if sandbox_running_ours(sport) {
        "green"
    } else {
        "amber"
    };
    json!({ "proxy": proxy, "sandbox": sandbox, "provider": provider,
        "proxy_port": pport, "sandbox_port": sport })
}

#[tauri::command]
async fn status(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || status_inner(app.state::<Mutex<AppState>>()))
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
async fn one_click_login(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let _operation = RUNTIME_OPERATION.lock().unwrap_or_else(|e| e.into_inner());
        launch_inner(app.clone(), app.state::<Mutex<AppState>>())
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn stop_all(app: tauri::AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let _operation = RUNTIME_OPERATION.lock().unwrap_or_else(|e| e.into_inner());
        stop_all_inner(app.clone(), app.state::<Mutex<AppState>>())
    })
    .await
    .map_err(|e| e.to_string())?
}

static RUNTIME_OPERATION: Mutex<()> = Mutex::new(());

#[tauri::command]
async fn set_mode(app: tauri::AppHandle, mode: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let _operation = RUNTIME_OPERATION.lock().unwrap_or_else(|e| e.into_inner());
        set_mode_inner(app.clone(), app.state::<Mutex<AppState>>(), mode)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn open_url(app: tauri::AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = RUNTIME_OPERATION.lock().unwrap_or_else(|e| e.into_inner());
        let cfg = config::load_from(&config::default_dir()).map_err(|e| e.to_string())?;
        if cfg.mode != "proxy" || !sandbox_running_ours(cfg.sandbox_port) {
            return Err("请先启动第三方 Science。".into());
        }
        let native_url = sandbox_url(cfg.sandbox_port);
        let state = app.state::<Mutex<AppState>>();
        let url = science_ui::entry(&app, &mut lock(&state), cfg.sandbox_port, &native_url)?;
        open_in_browser(&url)
    }).await.map_err(|_| "打开 Science 页面失败。".to_string())?
}

#[tauri::command]
fn run_doctor(app: tauri::AppHandle) -> Result<String, String> {
    let root = asset_root(&app).ok_or("找不到 scripts/doctor.sh（打包资源或仓库根均未命中）。")?;
    let cfg = config::load_from(&config::default_dir()).unwrap_or_default();
    let doctor = root.join("scripts/doctor.sh");
    let mut cmd = Command::new("bash");
    cmd.arg(&doctor)
        .env("CSSWITCH_PROVIDER", &cfg.provider)
        .env("CSSWITCH_PROXY_PORT", cfg.proxy_port.to_string())
        .env("CSSWITCH_SANDBOX_PORT", cfg.sandbox_port.to_string());
    // doctor 只做 -n 判空来报 key 有无。只让它知道「存在」，绝不把真实 key 传进其环境。
    if cfg.key_for(&cfg.provider).is_some() {
        cmd.env(key_env(&cfg.provider), "***present***");
    }
    let out = cmd.output().map_err(|e| e.to_string())?;
    let mut text = String::from_utf8_lossy(&out.stdout).to_string();
    let err = String::from_utf8_lossy(&out.stderr);
    if !err.trim().is_empty() {
        text.push_str("\n[stderr] ");
        text.push_str(err.trim());
    }
    Ok(text)
}

/// 当前 app 版本（供前端「检查更新」与页脚版本号用）。
#[tauri::command]
fn app_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// 打开 GitHub Releases 页（检查更新时用系统浏览器打开，浏览器走用户自己的代理）。
#[tauri::command]
fn open_release_page() -> Result<(), String> {
    open_in_browser("https://github.com/Vonfre/CSSwitch-Next/releases/latest")
}

/// 打开「报 bug」页（预填 bug 模板）；用系统浏览器，走用户自己的代理。
#[tauri::command]
fn report_bug() -> Result<(), String> {
    open_in_browser("https://github.com/Vonfre/CSSwitch-Next/issues/new?template=bug_report.yml")
}

/// 在访达里打开日志目录 `~/.csswitch/logs`，方便用户附到 bug 反馈里（先自查有无密钥）。
#[tauri::command]
fn open_logs() -> Result<(), String> {
    let dir = config::default_dir().join("logs");
    let _ = std::fs::create_dir_all(&dir);
    Command::new("open")
        .arg(&dir)
        .status()
        .map_err(|e| format!("打开日志目录失败：{e}"))?;
    Ok(())
}

#[tauri::command]
fn quit_app(app: tauri::AppHandle, state: State<'_, Mutex<AppState>>) -> Result<(), String> {
    // 默认：退 app 停代理、保留沙箱运行（spec §5.1）。
    {
        let mut st = lock(&state);
        kill_child(&mut st.proxy);
        kill_child(&mut st.science_ui);
        st.secret.clear();
    }
    app.exit(0);
    Ok(())
}

// ---------- 入口 ----------
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(updater::UpdateState::default())
        .manage(Mutex::new(AppState::default()))
        .invoke_handler(tauri::generate_handler![
            workspace_manage,
            updater::check_update,
            updater::download_update,
            updater::install_update,
            mcp_manage,
            science_ui::set_science_features,
            get_config,
            save_connection,
            select_connection,
            delete_connection,
            clear_connection_key,
            verify_connection,
            test_connection,
            list_models,
            read_usage,
            read_logs,
            diagnostics,
            set_config,
            set_mode,
            open_official,
            save_provider_key,
            start_proxy,
            verify_key,
            stop_all,
            one_click_login,
            status,
            open_url,
            run_doctor,
            app_version,
            open_release_page,
            report_bug,
            open_logs,
            quit_app
        ])
        .setup(|app| {
            // 正常桌面应用：进 Dock、走常规应用生命周期（默认 Regular 策略，
            // 不再设 Accessory）。窗口在 tauri.conf.json 里配了 decorations（标题栏
            // 三键：关闭/最小化/缩放）+ visible + center，启动即居中弹出、可拖动
            // （修 #4；标题栏自带拖动，顺带解决 #1 拖不动）。托盘图标已移除。

            // 关窗即退出：与「退出」按钮一致 —— 停代理、清 secret，保留沙箱运行
            // （spec §5.1）。不接这一步，从标题栏红叉关窗会绕过 quit_app 直接退，
            // 把代理子进程留成孤儿。
            if let Some(win) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                win.on_window_event(move |ev| {
                    if let tauri::WindowEvent::CloseRequested { .. } = ev {
                        let state = handle.state::<Mutex<AppState>>();
                        let mut st = lock(&state);
                        kill_child(&mut st.proxy);
                        kill_child(&mut st.science_ui);
                        st.secret.clear();
                    }
                });
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::{first_http_url, key_fingerprint, redact, sandbox_home};

    #[test]
    fn model_catalog_needs_no_selected_model_and_guards_saved_keys() {
        let mut cfg = super::config::Config::default();
        cfg.providers.insert("test".into(), super::config::ProviderCfg {
            protocol: "responses".into(), base_url: "https://example.com/v1".into(),
            key: "private-test-key".into(), ..Default::default()
        });
        let query = |url: &str, key: Option<&str>| super::ModelQuery {
            id: "test".into(), protocol: "responses".into(), base_url: url.into(), key: key.map(str::to_owned)
        };
        assert!(super::catalog_profile(query("https://example.com/v1/", None), &cfg).is_ok());
        assert!(super::catalog_profile(query("https://other.example.com/v1", None), &cfg).is_err());
        assert!(super::catalog_profile(query("https://other.example.com/v1", Some("new-test-key")), &cfg).is_ok());
        assert!(super::catalog_profile(query("https://example.com/v1", Some("bad\nkey")), &cfg).is_err());
    }

    #[test]
    fn public_connections_start_empty_and_do_not_inject_deleted_presets() {
        let mut cfg = super::config::Config::default();
        let empty = super::public_config(&cfg);
        assert_eq!(empty["connections"].as_array().unwrap().len(), 0);
        assert_eq!(empty["provider"], "");
        cfg.provider = "deepseek".into();
        assert_eq!(super::public_config(&cfg)["provider"], "");
        cfg.providers.insert("qwen".into(), super::config::ProviderCfg::preset("qwen").unwrap());
        assert_eq!(super::public_config(&cfg)["connections"].as_array().unwrap().len(), 1);
        cfg.remove_connection("qwen", false).unwrap();
        assert_eq!(super::public_config(&cfg)["connections"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn draft_probe_keeps_saved_config_and_restricts_key_reuse() {
        let mut cfg = super::config::Config::default();
        cfg.providers.insert("test".into(), super::config::ProviderCfg {
            protocol: "responses".into(), base_url: "https://example.com/v1".into(),
            key: "private-test-key".into(), ..Default::default()
        });
        let before = serde_json::to_vec(&cfg).unwrap();
        let input = |url: &str, key: Option<&str>| super::ConnectionInput {
            id: "test".into(), name: "Draft".into(), protocol: "responses".into(),
            base_url: url.into(), model: "draft-model".into(), quality_model: "".into(),
            fast_model: "".into(), max_tokens: 8192, key: key.map(str::to_owned)
        };
        let p = super::probe_profile(input("https://example.com/v1", None), &cfg).unwrap();
        assert_eq!(p.model, "draft-model");
        assert_eq!(p.key, "private-test-key");
        assert!(super::probe_profile(input("https://other.example.com/v1", None), &cfg).is_err());
        assert!(super::probe_profile(input("https://other.example.com/v1", Some("new-key")), &cfg).is_ok());
        let mut blank = input("https://example.com/v1", None);
        blank.model.clear();
        assert!(super::probe_profile(blank, &cfg).is_err());
        let mut bad = input("https://example.com/v1", None);
        bad.max_tokens = 0;
        assert!(super::probe_profile(bad, &cfg).is_err());
        assert_eq!(serde_json::to_vec(&cfg).unwrap(), before);
    }

    #[test]
    fn first_http_url_takes_only_first_valid_url() {
        // Science 的 `url` 命令输出两行：第一行是真 URL，第二行是「single-use…」说明。
        // 旧代码把整段 stdout 当 URL 交给 open → 换行+说明污染参数、nonce 不被消费 → 落登录页。
        // 只能取第一条合法 http(s) URL（修 0.2.1 Bug1）。
        let multi = "http://127.0.0.1:8990/setup?nonce=abc123\n\
                     This is a single-use link, expires in 60 seconds.";
        assert_eq!(
            first_http_url(multi).as_deref(),
            Some("http://127.0.0.1:8990/setup?nonce=abc123"),
            "多行输出必须只取第一行 URL，丢弃说明文字"
        );
        // 同一行 URL 后跟了说明，只取 URL token（URL 内不含空白）。
        let inline = "https://x.example/y?z=1  (single-use)";
        assert_eq!(
            first_http_url(inline).as_deref(),
            Some("https://x.example/y?z=1")
        );
        // 前导非 URL 行被跳过，取第一条 http 行。
        let lead = "Open this link in your browser:\nhttp://127.0.0.1:8990/a";
        assert_eq!(
            first_http_url(lead).as_deref(),
            Some("http://127.0.0.1:8990/a")
        );
        // 无任何 URL → None（sandbox_url 据此退回裸端口）。
        assert_eq!(first_http_url("no url here\nnor here"), None);
        // 单行纯 URL 原样返回。
        assert_eq!(
            first_http_url("http://127.0.0.1:8990").as_deref(),
            Some("http://127.0.0.1:8990")
        );
    }

    #[test]
    fn redact_scrubs_secret_and_is_noop_when_empty() {
        assert_eq!(
            redact("推理指向 http://127.0.0.1:18991/abcd1234 尾巴", "abcd1234"),
            "推理指向 http://127.0.0.1:18991/**** 尾巴"
        );
        assert_eq!(redact("原样返回", ""), "原样返回");
        assert!(!redact("leak abcd1234 leak abcd1234", "abcd1234").contains("abcd1234"));
    }

    #[test]
    fn key_fingerprint_stable_and_distinct() {
        // 同 key 稳定、异 key 不同：这是「换 key 触发代理重启」判断的基础（P1-2）。
        assert_eq!(key_fingerprint("sk-aaaa"), key_fingerprint("sk-aaaa"));
        assert_ne!(key_fingerprint("sk-aaaa"), key_fingerprint("sk-bbbb"));
        assert_ne!(key_fingerprint(""), key_fingerprint("x"));
    }

    #[test]
    fn sandbox_home_is_writable_under_config_dir() {
        // 沙箱状态目录必须在可写的 ~/.csswitch 下（不在只读的 .app 资源里）——P1-1。
        let h = sandbox_home();
        assert!(h.ends_with("sandbox/home"), "应以 sandbox/home 结尾：{h:?}");
        assert!(
            h.to_string_lossy().contains(".csswitch"),
            "应在 .csswitch 下：{h:?}"
        );
    }
}
