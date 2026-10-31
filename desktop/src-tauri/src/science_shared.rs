//! Shared Science history, with a separate local-only authentication store.
use std::fs;
use std::io::{Read, Write};
use crate::platform::PrivateOpenOptions;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    pub home: PathBuf,
    pub data_dir: PathBuf,
    pub config: PathBuf,
    pub port: u16,
}

fn uuid(s: &str) -> bool {
    s.len() == 36
        && s.bytes().enumerate().all(|(i, c)| match i {
            8 | 13 | 18 | 23 => c == b'-',
            _ => c.is_ascii_hexdigit(),
        })
}

fn read_private(path: &Path) -> Result<Option<String>, String> {
    super::config::assert_not_symlink(path).map_err(|e| e.to_string())?;
    let mut f = match fs::OpenOptions::new()
        .read(true)
        .nofollow()
        .open(path)
    {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("无法安全读取 Science 本地设置。".into()),
    };
    if !f.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("Science 设置不是普通文件。".into());
    }
    let mut s = String::new();
    Read::by_ref(&mut f)
        .take(1024 * 1024)
        .read_to_string(&mut s)
        .map_err(|_| "Science 设置无法读取。")?;
    if s.len() >= 1024 * 1024 {
        return Err("Science 设置文件过大。".into());
    }
    Ok(Some(s))
}

fn write_private(path: &Path, bytes: &[u8]) -> Result<(), String> {
    super::config::assert_not_symlink(path).map_err(|e| e.to_string())?;
    let tmp = path.with_extension(format!(
        "{}.tmp",
        super::proc::gen_secret().map_err(|e| e.to_string())?
    ));
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)
        .map_err(|_| "无法创建本地设置临时文件，请检查目录权限。")?;
    let result = (|| {
        f.write_all(bytes).map_err(|e| e.to_string())?;
        f.sync_all().map_err(|e| e.to_string())?;
        fs::rename(&tmp, path).map_err(|e| e.to_string())
    })();
    if result.is_err() {
        let _ = fs::remove_file(tmp);
    }
    result
}

pub fn home() -> Result<PathBuf, String> {
    crate::platform::home().map(|p| p.into_os_string())
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .ok_or("找不到本机主目录。".into())
}

pub fn preferred_binary(home: Option<&Path>, fallback: &Path) -> PathBuf {
    if let Some(home) = home {
        let updated = home.join(".claude-science/bin/claude-science");
        if crate::platform::executable(&updated) {
            return updated;
        }
    }
    fallback.into()
}

pub fn local_settings(home: &Path) -> Result<(PathBuf, toml::Value), String> {
    let native = home.join(".claude-science");
    let mut settings: toml::Value = match read_private(&native.join("config.toml"))? {
        Some(s) => {
            toml::from_str(&s).map_err(|_| "Science config.toml 格式不正确，未修改或启动服务。")?
        }
        None => toml::Value::Table(Default::default()),
    };
    if settings
        .get("data_dir_from")
        .and_then(toml::Value::as_str)
        .is_some_and(|s| !s.is_empty())
    {
        return Err("Science 正在迁移数据目录，请完成迁移后再切换。".into());
    }
    let dir = settings
        .get("data_dir")
        .and_then(toml::Value::as_str)
        .map(|s| {
            if s == "~" {
                home.to_path_buf()
            } else if let Some(tail) = s.strip_prefix("~/") {
                home.join(tail)
            } else {
                PathBuf::from(s)
            }
        })
        .unwrap_or(native);
    if !dir.is_absolute() {
        return Err("Science 数据目录必须是绝对路径。".into());
    }
    if settings
        .get("paths")
        .and_then(|p| p.get("auth_dir"))
        .and_then(toml::Value::as_str)
        .is_some_and(|p| Path::new(p) != home.join(".claude-science"))
    {
        return Err("检测到自定义 Science 认证目录，暂不自动接管，以免选择错误的历史身份。".into());
    }
    // Keep native settings intact; only the private launch copy is changed.
    settings
        .as_table_mut()
        .ok_or("Science 设置必须是 TOML 表。")?
        .insert(
            "data_dir".into(),
            toml::Value::String(dir.to_string_lossy().into_owned()),
        );
    Ok((dir, settings))
}

fn history_identity(
    native: &Path,
    data: &Path,
) -> Result<(Option<String>, Option<String>), String> {
    let active: Option<serde_json::Value> = read_private(&native.join("active-org.json"))?
        .map(|s| {
            serde_json::from_str(&s).map_err(|_| "Science 当前组织设置损坏，已中止以保留历史。")
        })
        .transpose()?;
    let mut org = active
        .as_ref()
        .and_then(|v| v.get("org_uuid"))
        .and_then(|v| v.as_str())
        .map(str::to_owned);
    if org.as_ref().is_some_and(|s| !uuid(s)) {
        return Err("Science 当前组织标识无效。".into());
    }
    if org.is_none() {
        let orgs = match fs::read_dir(data.join("orgs")) {
            Ok(entries) => entries
                .map(|e| e.map_err(|e| e.to_string()))
                .collect::<Result<Vec<_>, _>>()?
                .into_iter()
                .filter_map(|e| e.file_name().into_string().ok())
                .filter(|s| uuid(s))
                .collect::<Vec<_>>(),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(_) => return Err("无法检查历史组织目录。".into()),
        };
        match orgs.len() {
            0 => (),
            1 => org = orgs.into_iter().next(),
            _ => {
                return Err(
                    "有多个历史组织但没有活动组织，请在官方 Science 中选择原来的组织后重试。"
                        .into(),
                )
            }
        }
    }
    let explicit = active
        .as_ref()
        .and_then(|v| v.get("account_uuid"))
        .and_then(|v| v.as_str());
    if explicit.is_some_and(|s| !uuid(s)) {
        return Err("Science 当前账号标识无效。".into());
    }
    // Only inspect token filenames, never decrypt or copy the official OAuth tokens.
    let mut account = if let Some(id) = explicit {
        Some(id.into())
    } else {
        let ids = match fs::read_dir(native.join(".oauth-tokens")) {
            Ok(entries) => entries
                .map(|e| e.map_err(|e| e.to_string()))
                .collect::<Result<Vec<_>, _>>()?
                .into_iter()
                .filter_map(|e| e.file_name().into_string().ok())
                .filter_map(|s| {
                    s.strip_suffix(".enc")
                        .filter(|id| uuid(id))
                        .map(str::to_owned)
                })
                .collect::<Vec<_>>(),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(_) => return Err("无法读取本地账号标识。".into()),
        };
        match ids.len() {
            0 => None,
            1 => ids.into_iter().next(),
            _ => return Err("本地有多个账号且无法确认历史所属账号，已中止。".into()),
        }
    };
    // Sign-out can remove auth metadata while leaving all local projects. Read owner IDs only.
    if account.is_none() {
        if data.join("operon-cli.db").is_file() {
            account = history_owner(&data.join("operon-cli.db"))?;
        } else if let Some(org) = &org {
            account = history_owner(&data.join("orgs").join(org).join("operon-cli.db"))?;
        }
    }
    Ok((org, account))
}

fn history_owner(db: &Path) -> Result<Option<String>, String> {
    if !db.exists() {
        return Ok(None);
    }
    let python = super::proc::find_exe("python3").ok_or("查找历史所属账号需要 Python 3。")?;
    let out = crate::platform::command(python).args(["-I", "-c", "import json, sqlite3, sys\nfrom pathlib import Path\nwith sqlite3.connect(Path(sys.argv[1]).as_uri() + '?mode=ro', uri=True, timeout=2) as db:\n rows = db.execute(\"SELECT DISTINCT user_id FROM projects WHERE user_id IS NOT NULL AND user_id != '' LIMIT 2\").fetchall()\n print(json.dumps([row[0] for row in rows]))\n"])
        .arg(db).output().map_err(|_| "无法检查历史账号标识。")?;
    if !out.status.success() {
        return Err("无法只读检查历史账号标识，已中止以保留数据。".into());
    }
    let owners: Vec<String> =
        serde_json::from_slice(&out.stdout).map_err(|_| "历史账号标识无法解析。")?;
    match owners.as_slice() {
        [] => Ok(None),
        [owner] if owner == "local-dev" => Ok(None),
        [owner] if uuid(owner) => Ok(Some(owner.clone())),
        _ => Err("历史记录包含多个账号或无法识别的账号标识，未自动选择。".into()),
    }
}

pub fn prepare(home: &Path, root: &Path, port: u16) -> Result<Session, String> {
    let (data_dir, mut settings) = local_settings(home)?;
    // Enable the native updater only in our launch copy, preserving its channel settings.
    settings
        .as_table_mut()
        .unwrap()
        .entry("update")
        .or_insert_with(|| toml::Value::Table(Default::default()))
        .as_table_mut()
        .ok_or("Science update 设置无效。")?
        .insert("auto_update".into(), toml::Value::Boolean(true));
    let native = home.join(".claude-science");
    if root.starts_with(&native) || root.starts_with(&data_dir) || data_dir.starts_with(root) {
        return Err("CSSwitch 认证目录不能与 Science 对话数据目录重叠。".into());
    }
    let (org, account) = history_identity(&native, &data_dir)?;
    super::config::assert_not_symlink(root).map_err(|e| e.to_string())?;
    fs::create_dir_all(root).map_err(|e| e.to_string())?;
    crate::platform::permissions(root, 0o700).map_err(|e| e.to_string())?;
    let auth = root.join("auth");
    // Resolve the guarded auth root before copying any encryption material.
    let org = match org {
        Some(org) => org,
        None => super::oauth_forge::uuid_v4().map_err(|e| e.to_string())?,
    };
    super::oauth_forge::ensure_history_login(&auth, root, &native, &org, account.as_deref())?;
    // Existing encrypted MCP/user secrets must remain compatible across modes.
    // OAuth and JWT keys stay CSSwitch-owned; only data-encryption keys are carried over.
    if let Some(native_keys) = read_private(&native.join("encryption.key"))? {
        let private_keys =
            read_private(&auth.join("encryption.key"))?.ok_or("缺少本地认证密钥。")?;
        let replacements: std::collections::BTreeMap<_, _> = native_keys
            .lines()
            .filter_map(|l| l.split_once('='))
            .filter(|(k, _)| {
                matches!(
                    *k,
                    "ANTHROPIC_API_KEY_ENCRYPTION_KEY" | "USER_SECRET_ENCRYPTION_KEY"
                )
            })
            .map(|(k, v)| (k.trim(), v.trim()))
            .collect();
        let merged = private_keys
            .lines()
            .map(|l| {
                if let Some((k, _)) = l.split_once('=') {
                    if let Some(v) = replacements.get(k) {
                        return format!("{k}={v}");
                    }
                }
                l.to_string()
            })
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        write_private(&auth.join("encryption.key"), merged.as_bytes())?;
    }
    let paths = settings
        .as_table_mut()
        .unwrap()
        .entry("paths")
        .or_insert_with(|| toml::Value::Table(Default::default()));
    paths
        .as_table_mut()
        .ok_or("Science paths 设置无效。")?
        .insert(
            "auth_dir".into(),
            toml::Value::String(auth.to_string_lossy().into_owned()),
        );
    let config = root.join("science.toml");
    let text = toml::to_string(&settings).map_err(|_| "无法生成 Science 启动设置。")?;
    write_private(&config, text.as_bytes())?;
    let tools = root.join("tools");
    super::config::assert_not_symlink(&tools).map_err(|e| e.to_string())?;
    fs::create_dir_all(&tools).map_err(|e| e.to_string())?;
    crate::platform::permissions(&tools, 0o700).map_err(|e| e.to_string())?;
    // Scope the file-only auth store to this process, never change default/search Keychains.
    let shim = tools.join("security");
    write_private(&shim, b"#!/bin/sh\ncase \"$1\" in\n  find-generic-password) exit 44 ;;\n  add-generic-password|delete-generic-password|list-keychains|default-keychain|create-keychain|unlock-keychain|set-keychain-settings) exit 1 ;;\n  *) exec /usr/bin/security \"$@\" ;;\nesac\n")?;
    crate::platform::permissions(&shim, 0o500).map_err(|e| e.to_string())?;
    Ok(Session {
        home: home.into(),
        data_dir,
        config,
        port,
    })
}

/// Disable Claude-hosted MCP only in the private launch copy. Native config,
/// shared capability settings, grants and connector assignments are untouched.
pub fn apply_mcp_policy(session: &Session, local: bool) -> Result<(), String> {
    if !local { return Ok(()); }
    let mut settings: toml::Value = toml::from_str(
        &read_private(&session.config)?.ok_or("缺少 Science 启动配置。")?
    ).map_err(|_| "Science 启动配置无效。")?;
    let connectors = settings.as_table_mut().ok_or("Science 启动配置无效。")?
        .entry("connectors").or_insert_with(|| toml::Value::Table(Default::default()))
        .as_table_mut().ok_or("Science connectors 设置无效。")?;
    let disabled = connectors.entry("disabled").or_insert_with(|| toml::Value::Array(vec![]))
        .as_array_mut().ok_or("Science connectors.disabled 必须为数组。")?;
    for name in ["pubmed", "biorxiv", "chembl", "clinical-trials", "https://pubmed.mcp.claude.com", "https://hcls.mcp.claude.com"] {
        if !disabled.iter().any(|v| v.as_str() == Some(name)) {
            disabled.push(toml::Value::String(name.into()));
        }
    }
    write_private(&session.config, toml::to_string(&settings).map_err(|_| "无法保存 MCP 启动策略。")?.as_bytes())
}

pub fn marker(root: &Path) -> PathBuf {
    root.join("session.json")
}

pub fn history_summary(data: &Path) -> Result<serde_json::Value, String> {
    let python = super::proc::find_exe("python3").ok_or("统计历史记录需要 Python 3。")?;
    let output = crate::platform::command(python).args(["-I", "-c", "import json, sqlite3, sys\nfrom pathlib import Path\nroot = Path(sys.argv[1])\ncounts = dict(projects=0, conversations=0, messages=0)\nfor path in [p for p in [root / 'operon-cli.db', *sorted(root.glob('orgs/*/operon-cli.db'))] if p.is_file()]:\n with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2) as db:\n  counts['projects'] += db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]\n  counts['conversations'] += db.execute('SELECT COUNT(*) FROM frames WHERE parent_frame_id IS NULL').fetchone()[0]\n  counts['messages'] += db.execute('SELECT COUNT(*) FROM frame_messages').fetchone()[0]\nprint(json.dumps(counts))\n"])
        .arg(data).stderr(Stdio::null()).output().map_err(|_| "无法统计本地历史。")?;
    if !output.status.success() {
        return Err("无法只读统计本地历史。".into());
    }
    serde_json::from_slice(&output.stdout).map_err(|_| "本地历史统计无效。".into())
}

pub fn load(root: &Path) -> Result<Option<Session>, String> {
    read_private(&marker(root))?
        .map(|s| serde_json::from_str(&s).map_err(|_| "共享 Science 会话标记损坏。".into()))
        .transpose()
}

pub fn save(root: &Path, session: &Session) -> Result<(), String> {
    write_private(
        &marker(root),
        &serde_json::to_vec(session).map_err(|e| e.to_string())?,
    )
}

pub fn command(bin: &Path, session: &Session, action: &str) -> Command {
    let mut cmd = crate::platform::command(bin);
    cmd.arg(action)
        .arg("--data-dir")
        .arg(&session.data_dir)
        .arg("--config")
        .arg(&session.config)
        .env("HOME", &session.home)
        .env_remove("ANTHROPIC_API_KEY")
        .env_remove("ANTHROPIC_AUTH_TOKEN")
        .env_remove("ANTHROPIC_BASE_URL")
        .env_remove("BUN_OPTIONS");
    if let Some(root) = session.config.parent() {
        let path = std::env::var("PATH").unwrap_or_else(|_| "/usr/bin:/bin:/usr/sbin:/sbin".into());
        let mut paths = vec![root.join("tools")];
        paths.extend(std::env::split_paths(&path));
        if let Ok(path) = std::env::join_paths(paths) { cmd.env("PATH", path); }
    }
    cmd
}

pub fn status(bin: &Path, session: &Session) -> Result<serde_json::Value, String> {
    let output = command(bin, session, "status")
        .output()
        .map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err("无法查询 Science 服务，未执行切换。".into());
    }
    serde_json::from_slice(&output.stdout).map_err(|_| "Science 返回的服务状态无法解析。".into())
}

pub fn owns_status(value: &serde_json::Value, session: &Session) -> bool {
    if value.get("running").and_then(|v| v.as_bool()) != Some(true) {
        return false;
    }
    if value.get("port").and_then(|v| v.as_u64()) != Some(session.port as u64) {
        return false;
    }
    let Some(pid) = value.get("pid").and_then(|v| v.as_u64()) else {
        return false;
    };
    #[cfg(unix)]
    let Ok(out) = crate::platform::command("/bin/ps")
        .args(["-ww", "-p", &pid.to_string(), "-o", "command="])
        .output()
    else {
        return false;
    };
    #[cfg(windows)]
    let Ok(out) = crate::platform::command("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", &format!("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); (Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine")])
        .output() else { return false; };
    let cmd = String::from_utf8_lossy(&out.stdout);
    cmd.contains("claude-science")
        && cmd.contains(session.config.to_string_lossy().as_ref())
        && cmd.contains(session.data_dir.to_string_lossy().as_ref())
}

pub fn stop(bin: &Path, session: &Session) -> Result<(), String> {
    let output = command(bin, session, "stop")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| e.to_string())?;
    if !output.success() {
        return Err("Science 未能停止，请先关闭官方窗口与运行任务后重试。".into());
    }
    if status(bin, session)?
        .get("running")
        .and_then(|v| v.as_bool())
        == Some(true)
    {
        return Err("Science 被官方窗口重新启动，请关闭该窗口后重试。".into());
    }
    Ok(())
}

pub fn ensure_port_available(bin: &Path, session: &Session) -> Result<(), String> {
    if std::net::TcpListener::bind(("127.0.0.1", session.port)).is_ok() {
        return Ok(());
    }
    let value = status(bin, session)?;
    if value.get("running").and_then(|v| v.as_bool()) == Some(true)
        && value.get("port").and_then(|v| v.as_u64()) == Some(session.port as u64)
    {
        return Ok(());
    }
    Err(format!("端口 {} 被其他服务占用，本机 Science 未被停止。请先在旧版中停止隔离 Science，或在设置中更改 Science 端口。", session.port))
}

pub fn stop_owned(bin: &Path, root: &Path) -> Result<(), String> {
    if let Some(session) = load(root)? {
        if owns_status(&status(bin, &session)?, &session) {
            stop(bin, &session)?;
        }
        fs::remove_file(marker(root)).map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::{AtomicUsize, Ordering};
    const ORG: &str = "12345678-1234-4123-8123-123456789abc";
    const ACCOUNT: &str = "abcdefab-1234-4123-8123-123456789abc";
    static COUNTER: AtomicUsize = AtomicUsize::new(0);
    struct Fixture {
        base: PathBuf,
        home: PathBuf,
        root: PathBuf,
    }
    impl Fixture {
        fn new() -> Self {
            let base = std::env::temp_dir().join(format!(
                "csswitch-shared-{}-{}",
                std::process::id(),
                COUNTER.fetch_add(1, Ordering::SeqCst)
            ));
            let home = base.join("home");
            let root = home.join(".csswitch-next/shared-science");
            fs::create_dir_all(home.join(".claude-science/.oauth-tokens")).unwrap();
            Self { base, home, root }
        }
        fn native(&self) -> PathBuf {
            self.home.join(".claude-science")
        }
        fn identity(&self) {
            fs::write(
                self.native().join("active-org.json"),
                format!("{{\"org_uuid\":\"{ORG}\"}}\n"),
            )
            .unwrap();
            fs::write(
                self.native().join(format!(".oauth-tokens/{ACCOUNT}.enc")),
                "official-token-content-must-not-be-read",
            )
            .unwrap();
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.base);
        }
    }

    #[test]
    fn local_mcp_policy_is_private_and_preserves_native_settings() {
        let f = Fixture::new();
        f.identity();
        let native = f.native().join("config.toml");
        fs::write(&native, "[connectors]\ndisabled = ['user-disabled']\n").unwrap();
        let before = fs::read(&native).unwrap();
        let session = prepare(&f.home, &f.root, 8991).unwrap();
        apply_mcp_policy(&session, true).unwrap();
        apply_mcp_policy(&session, true).unwrap();
        let private: toml::Value = toml::from_str(&fs::read_to_string(&session.config).unwrap()).unwrap();
        let disabled = private["connectors"]["disabled"].as_array().unwrap();
        assert_eq!(disabled.len(), 7);
        assert!(disabled.iter().any(|v| v.as_str() == Some("user-disabled")));
        assert_eq!(fs::read(&native).unwrap(), before);
        let restored = prepare(&f.home, &f.root, 8991).unwrap();
        apply_mcp_policy(&restored, false).unwrap();
        let private: toml::Value = toml::from_str(&fs::read_to_string(&restored.config).unwrap()).unwrap();
        assert_eq!(private["connectors"]["disabled"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn binds_history_account_and_org_without_modifying_official_auth() {
        let f = Fixture::new();
        f.identity();
        let active = fs::read(f.native().join("active-org.json")).unwrap();
        let session = prepare(&f.home, &f.root, 8765).unwrap();
        assert_eq!(session.home, f.home);
        assert_eq!(session.data_dir, f.native());
        let login = super::super::oauth_forge::ensure_history_login(
            &f.root.join("auth"),
            &f.root,
            &f.native(),
            ORG,
            Some(ACCOUNT),
        )
        .unwrap();
        assert_eq!(login.org_uuid, ORG);
        assert_eq!(login.account_uuid, ACCOUNT);
        assert_eq!(
            fs::read(f.native().join("active-org.json")).unwrap(),
            active
        );
        assert_eq!(
            fs::read_to_string(f.native().join(format!(".oauth-tokens/{ACCOUNT}.enc"))).unwrap(),
            "official-token-content-must-not-be-read"
        );
        assert!(!f.native().join("encryption.key").exists());
        assert_eq!(
            fs::metadata(&session.config).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(&f.root).unwrap().permissions().mode() & 0o777,
            0o700
        );
    }

    #[test]
    fn honors_native_data_location_and_keeps_settings() {
        let f = Fixture::new();
        f.identity();
        let source = "data_dir = '~/science-history'\ndefault_model = 'custom-model'\n[conda]\nauto_install = false\n";
        fs::write(f.native().join("config.toml"), source).unwrap();
        let session = prepare(&f.home, &f.root, 8991).unwrap();
        assert_eq!(session.data_dir, f.home.join("science-history"));
        let parsed: toml::Value =
            toml::from_str(&fs::read_to_string(session.config).unwrap()).unwrap();
        assert_eq!(parsed["default_model"].as_str(), Some("custom-model"));
        assert_eq!(
            parsed["paths"]["auth_dir"].as_str(),
            f.root.join("auth").to_str()
        );
        assert_eq!(
            fs::read_to_string(f.native().join("config.toml")).unwrap(),
            source
        );
    }

    #[test]
    fn prefers_the_native_self_updated_binary_without_using_legacy_sandboxes() {
        let f = Fixture::new();
        let fallback = Path::new("/Applications/Claude Science.app/bin/claude-science");
        assert_eq!(preferred_binary(Some(&f.home), fallback), fallback);
        let updated = f.native().join("bin/claude-science");
        fs::create_dir_all(updated.parent().unwrap()).unwrap();
        fs::write(&updated, "#!/bin/sh\nexit 0\n").unwrap();
        crate::platform::permissions(&updated, 0o700).unwrap();
        assert_eq!(preferred_binary(Some(&f.home), fallback), updated);
    }

    #[test]
    fn enables_auto_update_without_changing_native_settings_or_channel() {
        let f = Fixture::new();
        let source = "[update]\nauto_update = false\nrelease_channel = 'stable'\npoll_ms = 600000\n";
        fs::write(f.native().join("config.toml"), source).unwrap();
        let session = prepare(&f.home, &f.root, 8991).unwrap();
        let parsed: toml::Value =
            toml::from_str(&fs::read_to_string(session.config).unwrap()).unwrap();
        assert_eq!(parsed["update"]["auto_update"].as_bool(), Some(true));
        assert_eq!(parsed["update"]["release_channel"].as_str(), Some("stable"));
        assert_eq!(parsed["update"]["poll_ms"].as_integer(), Some(600000));
        assert_eq!(
            fs::read_to_string(f.native().join("config.toml")).unwrap(),
            source
        );
        let fresh = Fixture::new();
        let session = prepare(&fresh.home, &fresh.root, 8991).unwrap();
        let parsed: toml::Value =
            toml::from_str(&fs::read_to_string(session.config).unwrap()).unwrap();
        assert_eq!(parsed["update"]["auto_update"].as_bool(), Some(true));
        assert!(!fresh.native().join("config.toml").exists());
    }

    #[test]
    fn fresh_home_needs_no_official_login_and_leaves_legacy_history_alone() {
        let f = Fixture::new();
        let legacy = f.home.join(".csswitch-next/sandbox/home/.claude-science");
        fs::create_dir_all(&legacy).unwrap();
        fs::write(legacy.join("sentinel"), "old-history").unwrap();
        prepare(&f.home, &f.root, 8991).unwrap();
        assert_eq!(
            fs::read_to_string(legacy.join("sentinel")).unwrap(),
            "old-history"
        );
        assert!(!f.native().join("active-org.json").exists());
        assert!(!f.native().join("encryption.key").exists());
    }

    #[test]
    fn ambiguous_orgs_and_invalid_settings_fail_before_auth_writes() {
        let f = Fixture::new();
        for id in [ORG, ACCOUNT] {
            fs::create_dir_all(f.native().join("orgs").join(id)).unwrap();
        }
        assert!(prepare(&f.home, &f.root, 8991)
            .unwrap_err()
            .contains("多个历史组织"));
        assert!(!f.root.exists());
        fs::write(f.native().join("config.toml"), "not = [valid").unwrap();
        assert!(prepare(&f.home, &f.root, 8991).is_err());
    }

    #[test]
    fn data_encryption_survives_mode_switch_but_oauth_keys_are_not_copied() {
        let f = Fixture::new();
        f.identity();
        let native = "USER_SECRET_ENCRYPTION_KEY=fixture-user-key\nANTHROPIC_API_KEY_ENCRYPTION_KEY=fixture-api-key\nOAUTH_ENCRYPTION_KEY=official-oauth-must-stay-private\nJWT_SIGNING_SECRET=official-jwt\n";
        fs::write(f.native().join("encryption.key"), native).unwrap();
        prepare(&f.home, &f.root, 8991).unwrap();
        let private = fs::read_to_string(f.root.join("auth/encryption.key")).unwrap();
        assert!(private.contains("USER_SECRET_ENCRYPTION_KEY=fixture-user-key"));
        assert!(private.contains("ANTHROPIC_API_KEY_ENCRYPTION_KEY=fixture-api-key"));
        assert!(!private.contains("official-oauth"));
        assert!(!private.contains("official-jwt"));
        assert_eq!(
            fs::read_to_string(f.native().join("encryption.key")).unwrap(),
            native
        );
    }

    #[test]
    fn refuses_auth_symlink_and_migration_in_progress() {
        let f = Fixture::new();
        f.identity();
        fs::create_dir_all(&f.root).unwrap();
        std::os::unix::fs::symlink(f.native(), f.root.join("auth")).unwrap();
        assert!(prepare(&f.home, &f.root, 8991).is_err());
        fs::write(
            f.native().join("config.toml"),
            "data_dir_from = '/old/location'\n",
        )
        .unwrap();
        assert!(local_settings(&f.home).unwrap_err().contains("迁移"));
    }

    #[test]
    fn commands_keep_real_home_and_data_but_separate_auth_config() {
        let f = Fixture::new();
        f.identity();
        let session = prepare(&f.home, &f.root, 8991).unwrap();
        for action in ["serve", "stop", "status", "url"] {
            let cmd = command(Path::new("/fake-science"), &session, action);
            let args: Vec<_> = cmd
                .get_args()
                .map(|s| s.to_string_lossy().into_owned())
                .collect();
            assert_eq!(
                args,
                vec![
                    action.to_string(),
                    "--data-dir".into(),
                    f.native().to_string_lossy().into_owned(),
                    "--config".into(),
                    session.config.to_string_lossy().into_owned()
                ]
            );
            assert!(cmd
                .get_envs()
                .any(|(k, v)| k == "HOME" && v == Some(f.home.as_os_str())));
            assert!(!args
                .iter()
                .any(|s| s.contains("skip-approvals") || s.contains("no-sandbox")));
        }
        save(&f.root, &session).unwrap();
        assert_eq!(load(&f.root).unwrap().unwrap().data_dir, f.native());
        assert!(!owns_status(
            &serde_json::json!({"running":true,"pid":std::process::id(),"port":8991}),
            &session
        ));
    }

    #[test]
    fn occupied_legacy_port_is_rejected_before_stopping_native_science() {
        let f = Fixture::new();
        f.identity();
        let occupied = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = occupied.local_addr().unwrap().port();
        let session = prepare(&f.home, &f.root, port).unwrap();
        let fake = f.base.join("fake-science");
        fs::write(
            &fake,
            "#!/bin/sh\nif [ \"$1\" != status ]; then exit 42; fi\nprintf '{\"running\":false}'\n",
        )
        .unwrap();
        crate::platform::permissions(&fake, 0o700).unwrap();
        assert!(ensure_port_available(&fake, &session)
            .unwrap_err()
            .contains("未被停止"));
        fs::write(
            &fake,
            format!("#!/bin/sh\nprintf '{{\"running\":true,\"port\":{port}}}'\n"),
        )
        .unwrap();
        assert!(ensure_port_available(&fake, &session).is_ok());
        drop(occupied);
        assert!(ensure_port_available(&fake, &session).is_ok());
    }

    #[test]
    #[ignore = "launches the installed Science binary with synthetic HOME and loopback-only network"]
    fn installed_science_preserves_history_across_auth_stores() {
        let f = Fixture::new();
        let test_bin = std::env::var_os("CSSWITCH_TEST_SCIENCE_BIN")
            .map(PathBuf::from).unwrap_or_else(|| PathBuf::from(super::super::SCIENCE_BIN));
        let bin = test_bin.as_path();
        assert!(bin.is_file());
        super::super::oauth_forge::ensure_history_login(
            &f.native(),
            &f.home,
            &f.base.join("forbidden-auth"),
            ORG,
            Some(ACCOUNT),
        )
        .unwrap();
        fs::write(
            f.native().join("config.toml"),
            "[conda]\nauto_install = false\n[sandbox]\nhard_fail = false\n",
        )
        .unwrap();
        let oauth = fs::read(f.native().join(format!(".oauth-tokens/{ACCOUNT}.enc"))).unwrap();
        let keys = fs::read(f.native().join("encryption.key")).unwrap();
        let active = fs::read(f.native().join("active-org.json")).unwrap();
        let port = {
            let socket = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            socket.local_addr().unwrap().port()
        };
        let original = Session {
            home: f.home.clone(),
            data_dir: f.native(),
            config: f.native().join("config.toml"),
            port,
        };
        struct Cleanup<'a> {
            bin: &'a Path,
            session: Session,
        }
        impl Drop for Cleanup<'_> {
            fn drop(&mut self) {
                let _ = stop(self.bin, &self.session);
            }
        }
        let _cleanup = Cleanup {
            bin,
            session: original.clone(),
        };
        let test_tools = f.base.join("tools");
        fs::create_dir_all(&test_tools).unwrap();
        fs::write(
            test_tools.join("security"),
            "#!/bin/sh\nif [ \"$1\" = find-generic-password ]; then exit 44; fi\nexit 1\n",
        )
        .unwrap();
        fs::set_permissions(
            test_tools.join("security"),
            fs::Permissions::from_mode(0o700),
        )
        .unwrap();
        let launch = |session: &Session| {
            let log = fs::File::create(f.base.join("science-test.log")).unwrap();
            let mut cmd = crate::platform::command("/usr/bin/sandbox-exec");
            cmd.args(["-p", "(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip \"localhost:*\"))"])
                .arg(bin).arg("serve").arg("--data-dir").arg(&session.data_dir).arg("--config").arg(&session.config)
                .args(["--port", &port.to_string(), "--sandbox-port", "0", "--detached", "--no-browser", "--no-auto-update"])
                .env("HOME", &f.home).env("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
                .env("https_proxy", "http://127.0.0.1:9").env("HTTPS_PROXY", "http://127.0.0.1:9")
                .env("OPERON_DISABLE_NONESSENTIAL_TRAFFIC", "1").env_remove("ANTHROPIC_API_KEY").env_remove("ANTHROPIC_AUTH_TOKEN")
                .env("PATH", format!("{}:/usr/bin:/bin:/usr/sbin:/sbin", test_tools.display()))
                .env_remove("BUN_OPTIONS").stdout(log.try_clone().unwrap()).stderr(log);
            assert!(
                cmd.status().unwrap().success(),
                "{}",
                fs::read_to_string(f.base.join("science-test.log")).unwrap()
            );
            for _ in 0..100 {
                if status(bin, session)
                    .unwrap()
                    .get("running")
                    .and_then(|v| v.as_bool())
                    == Some(true)
                {
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            panic!("synthetic Science did not become ready");
        };
        let cookies = f.base.join("cookies");
        let login = |session: &Session| {
            let output = command(bin, session, "url").output().unwrap();
            let link =
                super::super::first_http_url(&String::from_utf8_lossy(&output.stdout)).unwrap();
            let url = url::Url::parse(&link).unwrap();
            let base = format!(
                "{}://{}:{}",
                url.scheme(),
                url.host_str().unwrap(),
                url.port().unwrap()
            );
            let nonce = url
                .query_pairs()
                .find(|(k, _)| k == "nonce")
                .unwrap()
                .1
                .into_owned();
            assert!(crate::platform::command("curl")
                .args(["--noproxy", "*", "--max-time", "10", "-sS", "-f", "-c"])
                .arg(&cookies)
                .args(["-H", "Content-Type: application/json", "--data"])
                .arg(format!("{{\"nonce\":\"{nonce}\",\"dest\":\"/\"}}"))
                .arg(format!("{base}/api/auth/nonce"))
                .stdout(Stdio::null())
                .status()
                .unwrap()
                .success());
            assert!(crate::platform::command("curl")
                .args(["--noproxy", "*", "--max-time", "10", "-sS", "-f", "-c"])
                .arg(&cookies)
                .arg("-b")
                .arg(&cookies)
                .arg("-H")
                .arg(format!("Origin: {base}"))
                .arg(format!("{base}/api/csrf"))
                .stdout(Stdio::null())
                .status()
                .unwrap()
                .success());
            base
        };
        let request = |base: &str, path: &str, body: Option<&str>| {
            let text = fs::read_to_string(&cookies).unwrap();
            let csrf = text
                .lines()
                .filter_map(|l| {
                    let parts = l.split('\t').collect::<Vec<_>>();
                    if parts.len() == 7 && parts[5] == "operon_csrf" {
                        Some(parts[6])
                    } else {
                        None
                    }
                })
                .next()
                .unwrap_or("");
            let mut cmd = crate::platform::command("curl");
            cmd.args(["--noproxy", "*", "--max-time", "10", "-sS", "-f", "-b"])
                .arg(&cookies)
                .arg("-H")
                .arg(format!("Origin: {base}"))
                .arg("-H")
                .arg(format!("x-operon-csrf: {csrf}"));
            if let Some(body) = body {
                cmd.args(["-H", "Content-Type: application/json", "--data", body]);
            }
            let out = cmd.arg(format!("{base}{path}")).output().unwrap();
            assert!(
                out.status.success(),
                "{path}: {}",
                String::from_utf8_lossy(&out.stderr)
            );
            String::from_utf8(out.stdout).unwrap()
        };
        launch(&original);
        let base = login(&original);
        let created = request(
            &base,
            "/api/projects",
            Some("{\"name\":\"shared-history-fixture\"}"),
        );
        assert!(created.contains("shared-history-fixture"));
        let project: serde_json::Value = serde_json::from_str(&created).unwrap();
        let id = project
            .get("project_id")
            .or_else(|| project.get("id"))
            .and_then(|v| v.as_str())
            .expect("project id");
        request(
            &base,
            "/api/frames",
            Some(&format!("{{\"project_id\":\"{id}\"}}")),
        );
        let frames_path = format!("/api/frames?project_id={id}&root_only=true");
        let frames_before = request(&base, &frames_path, None);
        assert!(
            frames_before.contains(id),
            "fixture conversation must exist"
        );
        stop(bin, &original).unwrap();
        let shared = prepare(&f.home, &f.root, port).unwrap();
        save(&f.root, &shared).unwrap();
        launch(&shared);
        assert!(owns_status(&status(bin, &shared).unwrap(), &shared));
        let base = login(&shared);
        assert!(request(&base, "/api/auth/status", None).contains("\"authenticated\":true"));
        assert!(request(&base, "/api/projects", None).contains("shared-history-fixture"));
        assert_eq!(request(&base, &frames_path, None), frames_before);
        assert_eq!(
            fs::read(f.native().join(format!(".oauth-tokens/{ACCOUNT}.enc"))).unwrap(),
            oauth
        );
        assert_eq!(fs::read(f.native().join("encryption.key")).unwrap(), keys);
        assert_eq!(
            fs::read(f.native().join("active-org.json")).unwrap(),
            active
        );
        stop_owned(bin, &f.root).unwrap();
        launch(&original);
        let base = login(&original);
        assert!(request(&base, "/api/projects", None).contains("shared-history-fixture"));
        assert_eq!(request(&base, &frames_path, None), frames_before);
        stop(bin, &original).unwrap();
        // Simulate native sign-out: history is retained, but there is no native login to reuse.
        fs::remove_file(f.native().join(format!(".oauth-tokens/{ACCOUNT}.enc"))).unwrap();
        fs::remove_file(f.native().join("active-org.json")).unwrap();
        let logged_out = prepare(&f.home, &f.root, port).unwrap();
        save(&f.root, &logged_out).unwrap();
        launch(&logged_out);
        let base = login(&logged_out);
        assert!(request(&base, "/api/auth/status", None).contains("\"authenticated\":true"));
        assert_eq!(request(&base, &frames_path, None), frames_before);
        assert!(!f.native().join("active-org.json").exists());
        assert!(!f
            .native()
            .join(format!(".oauth-tokens/{ACCOUNT}.enc"))
            .exists());
    }
}
