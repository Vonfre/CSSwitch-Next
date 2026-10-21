//! Local browser entry point. No translation requests leave the machine.
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::time::Duration;
use tauri::Manager;

pub fn entry(
    app: &tauri::AppHandle,
    state: &mut super::AppState,
    upstream: u16,
    native_url: &str,
) -> Result<String, String> {
    let mut link = url::Url::parse(native_url).map_err(|_| "Science 页面地址无效。")?;
    if link.scheme() != "http"
        || !matches!(link.host_str(), Some("localhost" | "127.0.0.1"))
        || link.port() != Some(upstream)
    {
        return Err("Science 页面地址不属于当前本地服务。".into());
    }
    let alive = state
        .science_ui
        .as_mut()
        .is_some_and(|child| matches!(child.try_wait(), Ok(None)));
    if !alive || state.science_ui_upstream != upstream {
        super::kill_child(&mut state.science_ui);
        let root = super::asset_root(app).ok_or("找不到 Science 页面插件资源。")?;
        let ready = super::config::default_dir().join(format!(
            "ui-{}.json",
            super::proc::gen_secret().map_err(|_| "无法初始化页面入口。")?
        ));
        let (child, port) = start_proxy(
            &root,
            &super::config::default_dir().join("config.json"),
            &ready,
            upstream,
        )?;
        state.science_ui = Some(child);
        state.science_ui_port = port;
        state.science_ui_upstream = upstream;
    }
    link.set_host(Some("127.0.0.1"))
        .map_err(|_| "无法生成页面入口。")?;
    link.set_port(Some(state.science_ui_port))
        .map_err(|_| "无法生成页面入口。")?;
    Ok(link.into())
}

fn start_proxy(
    root: &Path,
    config: &Path,
    ready: &Path,
    upstream: u16,
) -> Result<(Child, u16), String> {
    let py = super::proc::find_exe("python3").ok_or("Science 页面插件需要 Python 3。")?;
    let mut child = Command::new(py)
        .arg("-B")
        .arg(root.join("scripts/science-ui-proxy.py"))
        .arg("--upstream-port")
        .arg(upstream.to_string())
        .arg("--config")
        .arg(config)
        .arg("--ready")
        .arg(ready)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(if cfg!(test) { Stdio::inherit() } else { Stdio::null() })
        .spawn()
        .map_err(|_| "无法启动 Science 页面入口。")?;
    let mut port = None;
    // Allow a cold Python interpreter to start on slower machines. Readiness
    // requires a successful HTTP probe, not just the file announcing its port.
    for _ in 0..150 {
        if let Ok(bytes) = std::fs::read(ready) {
            port = serde_json::from_slice::<serde_json::Value>(&bytes)
                .ok()
                .and_then(|v| v["port"].as_u64())
                .and_then(|p| u16::try_from(p).ok());
            if port.is_some_and(|p| super::proc::http_health_path(p, "/__csswitch/health", 400)) {
                break;
            }
        }
        if !matches!(child.try_wait(), Ok(None)) {
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let _ = std::fs::remove_file(ready);
    let Some(port) = port.filter(|p| super::proc::http_health_path(*p, "/__csswitch/health", 400))
    else {
        let _ = child.kill();
        let _ = child.wait();
        return Err("Science 页面入口启动失败，请重新打开 Science。".into());
    };
    Ok((child, port))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn browser_entry_starts_with_its_own_health_endpoint() {
        let temp = tempfile::tempdir().unwrap();
        let ready = temp.path().join("ready.json");
        let config = temp.path().join("config.json");
        std::fs::write(&config, r#"{"science_zh":false}"#).unwrap();
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .parent()
            .unwrap();
        assert!(root.join("scripts/science-ui-proxy.py").is_file());
        let python = super::super::proc::find_exe("python3").unwrap();
        eprintln!("Page entry Python: {}", python.display());
        // Run the production launcher and actual Python proxy, without a Science
        // daemon. Entry readiness must never depend on the upstream /health route.
        let (mut child, port) =
            start_proxy(root, &config, &ready, 0).expect("page entry should start");
        let alive = matches!(child.try_wait(), Ok(None));
        let _ = child.kill();
        let _ = child.wait();
        assert!(alive);
        assert!(port > 0);
        assert!(!ready.exists());
    }
}

pub fn running_policy(config: &Path) -> Option<bool> {
    let text = std::fs::read_to_string(config).ok()?;
    let settings: toml::Value = toml::from_str(&text).ok()?;
    let Some(disabled) = settings
        .get("connectors")
        .and_then(|v| v.get("disabled"))
        .and_then(toml::Value::as_array)
    else {
        return Some(false);
    };
    Some(
        ["pubmed", "biorxiv", "chembl", "clinical-trials"]
            .iter()
            .all(|name| disabled.iter().any(|v| v.as_str() == Some(name))),
    )
}

#[tauri::command]
pub async fn set_science_features(
    app: tauri::AppHandle,
    local_mcp: bool,
    zh: bool,
    local_files: Option<bool>,
) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = super::RUNTIME_OPERATION.lock().unwrap_or_else(|e| e.into_inner());
        super::config::update(&super::config::default_dir(), |cfg| {
            cfg.science_local_mcp = local_mcp; cfg.science_zh = zh;
            if let Some(enabled) = local_files { cfg.science_local_files = enabled; }
        }).map_err(|_| "无法保存 Science 插件设置。")?;
        let cfg = super::config::load_from(&super::config::default_dir()).map_err(|_| "无法读取插件设置。")?;
        let running = super::sandbox_running_ours(cfg.sandbox_port);
        let pending = running && super::science_shared::load(&super::shared_root())?.and_then(|s| running_policy(&s.config)) != Some(local_mcp);
        let state = app.state::<std::sync::Mutex<super::AppState>>();
        let st = super::lock(&state);
        Ok(serde_json::json!({"mcp_pending": pending, "ui_open": st.science_ui.is_some(), "message": if pending { "插件设置已保存；MCP 策略在下次启动 Science 时生效。" } else { "设置已保存；通过 CSSwitch 打开的 Science 页面会自动应用插件设置。" }}))
    }).await.map_err(|_| "保存插件设置失败。".to_string())?
}
