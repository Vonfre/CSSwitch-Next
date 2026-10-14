//! Only native code handles release URLs, signed downloads and installation.
use std::time::{Duration, Instant};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{ipc::Channel, Manager, State};
use tauri_plugin_updater::{Update, UpdaterExt};

const RELEASE_API: &str = "https://api.github.com/repos/Vonfre/CSSwitch-Next/releases";

#[derive(Deserialize)]
struct Release {
    assets: Vec<Asset>,
}

#[derive(Deserialize)]
struct Asset {
    id: u64,
    name: String,
}

fn asset_url(asset: &Asset) -> String {
    format!("{RELEASE_API}/assets/{}", asset.id)
}

fn archive_api_url(release: &Release, original: &url::Url) -> Result<url::Url, String> {
    let filename = original.path_segments().and_then(|mut p| p.next_back())
        .ok_or("更新包地址无效。")?;
    let archive = release.assets.iter().find(|a| a.name == filename)
        .ok_or("Release 中缺少当前架构的更新包。")?;
    asset_url(archive).parse().map_err(|_| "更新包地址无效。".to_string())
}

/// Public API needs no GitHub login. Use it when the github.com download domain is unreachable.
/// The same pinned key and signed-version checks still validate the actual downloaded bytes.
async fn check_via_api<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> Result<Option<Update>, String> {
    if rustls::crypto::CryptoProvider::get_default().is_none() {
        let _ = rustls::crypto::ring::default_provider().install_default();
    }
    let client = reqwest::Client::builder().timeout(Duration::from_secs(20))
        .user_agent("CSSwitch-Next-Updater").build().map_err(|e| e.to_string())?;
    let release: Release = client.get(format!("{RELEASE_API}/latest"))
        .send().await.map_err(|e| e.to_string())?.error_for_status().map_err(|e| e.to_string())?
        .json().await.map_err(|e| e.to_string())?;
    let manifest = release.assets.iter().find(|a| a.name == "latest.json")
        .ok_or("Release 中缺少更新清单。")?;
    let mut update = app.updater_builder().timeout(Duration::from_secs(20))
        .configure_client(|client| client.connect_timeout(Duration::from_secs(10)).read_timeout(Duration::from_secs(30)))
        .endpoints(vec![asset_url(manifest).parse().map_err(|_| "更新地址无效。")?])
        .map_err(|e| e.to_string())?.header("Accept", "application/octet-stream")
        .map_err(|e| e.to_string())?.build().map_err(|e| e.to_string())?
        .check().await.map_err(|e| e.to_string())?;
    if let Some(update) = &mut update {
        // Resolve the selected architecture's archive through the same public API.
        update.download_url = archive_api_url(&release, &update.download_url)?;
    }
    Ok(update)
}

async fn verified_download(update: &Update, channel: &Channel<Progress>) -> Result<Vec<u8>, String> {
    let mut downloaded = 0;
    let mut last = Instant::now();
    let _ = channel.send(Progress { downloaded: 0, total: None });
    update.download(|chunk, total| {
        downloaded += chunk as u64;
        if last.elapsed() >= Duration::from_millis(100) || Some(downloaded) == total {
            let _ = channel.send(Progress { downloaded, total });
            last = Instant::now();
        }
    }, || {}).await.map_err(|e| e.to_string()).inspect(|_| {
        let _ = channel.send(Progress { downloaded, total: Some(downloaded) });
    })
}

#[derive(Default)]
pub struct UpdateState(tauri::async_runtime::Mutex<Pending>);

#[derive(Default)]
struct Pending {
    update: Option<Update>,
    bytes: Option<Vec<u8>>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Progress {
    downloaded: u64,
    total: Option<u64>,
}

fn metadata(pending: &Pending) -> Value {
    match &pending.update {
        Some(update) => json!({"current": env!("CARGO_PKG_VERSION"), "version": update.version,
            "notes": update.body, "available": true, "downloaded": pending.bytes.is_some()}),
        None => json!({"current": env!("CARGO_PKG_VERSION"), "available": false}),
    }
}

#[tauri::command]
pub async fn check_update(app: tauri::AppHandle, state: State<'_, UpdateState>) -> Result<Value, String> {
    let mut pending = state.0.try_lock().map_err(|_| "更新操作正在进行，请稍后重试。")?;
    let direct = app.updater_builder().timeout(Duration::from_secs(10))
        .configure_client(|client| client.connect_timeout(Duration::from_secs(10)).read_timeout(Duration::from_secs(30))).build()
        .map_err(|e| format!("初始化更新失败：{e}"))?.check().await;
    let update = match direct {
        Ok(update) => update,
        Err(_) => check_via_api(&app).await
            .map_err(|e| format!("无法检查更新，请检查网络或系统代理后重试：{e}"))?,
    };
    // Keep an already verified download if this is still the same release.
    if pending.update.as_ref().map(|u| (&u.version, &u.signature))
        != update.as_ref().map(|u| (&u.version, &u.signature)) {
        pending.bytes = None;
    }
    pending.update = update;
    Ok(metadata(&pending))
}

#[tauri::command]
pub async fn download_update(app: tauri::AppHandle, state: State<'_, UpdateState>, on_progress: Channel<Progress>) -> Result<Value, String> {
    let mut pending = state.0.try_lock().map_err(|_| "更新操作正在进行，请稍后重试。")?;
    let mut update = pending.update.clone().ok_or("请先检查更新。")?;
    update.timeout = Some(Duration::from_secs(600));
    let bytes = match verified_download(&update, &on_progress).await {
        Ok(bytes) => bytes,
        Err(first_error) => {
            let fallback = check_via_api(&app).await.ok().flatten()
                .filter(|u| u.version == update.version && u.signature == update.signature);
            let Some(mut fallback) = fallback else {
                return Err(format!("下载或签名验证失败，未安装更新：{first_error}"));
            };
            fallback.timeout = Some(Duration::from_secs(600));
            let bytes = verified_download(&fallback, &on_progress).await
                .map_err(|e| format!("下载或签名验证失败，未安装更新：{e}"))?;
            pending.update = Some(fallback);
            bytes
        }
    };
    pending.bytes = Some(bytes);
    Ok(metadata(&pending))
}

#[tauri::command]
pub async fn install_update(app: tauri::AppHandle, state: State<'_, UpdateState>) -> Result<(), String> {
    let pending = state.0.try_lock().map_err(|_| "更新操作正在进行，请稍后重试。")?;
    let update = pending.update.clone().ok_or("请先检查更新。")?;
    let bytes = pending.bytes.as_ref().ok_or("请先下载并验证更新。")?.clone();
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let _operation = super::RUNTIME_OPERATION.lock().unwrap_or_else(|e| e.into_inner());
        let runtime = handle.state::<std::sync::Mutex<super::AppState>>();
        let mut runtime = super::lock(&runtime);
        if let Some(child) = runtime.proxy.as_mut() {
            if child.try_wait().map_err(|e| e.to_string())?.is_none() {
                return Err("请先在提供商页面停止网关与 Science，结束研究任务后再安装更新。".to_string());
            }
        }
        // Do not attempt to overwrite a development binary or an app on a mounted DMG.
        let executable = std::env::current_exe().map_err(|e| e.to_string())?;
        if !executable.ancestors().any(|p| p.extension().is_some_and(|e| e == "app"))
            || executable.starts_with("/Volumes") {
            return Err("请先将应用安装到本机应用目录，再使用应用内更新。".to_string());
        }
        update.install(bytes).map_err(|e| format!("安装失败，可重试：{e}"))?;
        super::kill_child(&mut runtime.proxy);
        runtime.secret.clear();
        // Hold the lifecycle lock until restart so a new gateway cannot start in between.
        handle.restart();
    }).await.map_err(|_| "安装更新任务异常。".to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::{Read, Write}, net::TcpListener};

    #[test]
    fn public_api_resolves_only_the_selected_archive() {
        let release: Release = serde_json::from_value(json!({"assets": [
            {"id": 10, "name": "app-arm64.app.tar.gz"},
            {"id": 20, "name": "app-x64.app.tar.gz"}
        ]})).unwrap();
        let url = |name| format!("https://github.com/owner/repo/releases/download/v1.0.0/{name}").parse().unwrap();
        assert_eq!(archive_api_url(&release, &url("app-arm64.app.tar.gz")).unwrap().as_str(),
            format!("{RELEASE_API}/assets/10"));
        assert_eq!(archive_api_url(&release, &url("app-x64.app.tar.gz")).unwrap().as_str(),
            format!("{RELEASE_API}/assets/20"));
        assert!(archive_api_url(&release, &url("missing.app.tar.gz")).is_err());
        assert!(serde_json::from_value::<Release>(json!({"message": "rate limited"})).is_err());
    }

    #[test]
    #[ignore = "uses the live public GitHub release API without authentication"]
    fn live_public_api_fallback_download() {
        let mut context = tauri::test::mock_context(tauri::test::noop_assets());
        let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        context.config_mut().plugins = serde_json::from_value(config["plugins"].clone()).unwrap();
        let app = tauri::test::mock_builder().plugin(tauri_plugin_updater::Builder::new().build())
            .build(context).unwrap();
        tauri::async_runtime::block_on(async {
            let mut update = check_via_api(app.handle()).await.unwrap().unwrap();
            assert_eq!(update.download_url.host_str(), Some("api.github.com"));
            update.timeout = Some(Duration::from_secs(60));
            let bytes = update.download(|_, _| {}, || {}).await.unwrap();
            assert!(!bytes.is_empty());
        });
    }

    /// Exercises the actual plugin and native macOS installer against an isolated app copy.
    /// No production app, account, gateway or Science process is touched.
    #[test]
    #[ignore = "requires CSSWITCH_TEST_UPDATE_ARCHIVE from a signed local build"]
    fn signed_download_and_macos_install() {
        let archive = std::env::var("CSSWITCH_TEST_UPDATE_ARCHIVE").unwrap();
        let bytes = std::fs::read(&archive).unwrap();
        let signature = std::fs::read_to_string(format!("{archive}.sig")).unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let manifest = json!({"version": env!("CARGO_PKG_VERSION"), "platforms": {
            "darwin-aarch64": {"url": format!("{base}/archive"), "signature": signature.trim()},
            "darwin-x86_64": {"url": format!("{base}/archive"), "signature": signature.trim()}
        }}).to_string().into_bytes();
        let archive_bytes = bytes.clone();
        let server = std::thread::spawn(move || {
            for _ in 0..4 {
                let (mut stream, _) = listener.accept().unwrap();
                stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                let mut request = [0; 4096];
                let length = stream.read(&mut request).unwrap();
                let request = String::from_utf8_lossy(&request[..length]);
                let body = if request.starts_with("GET /manifest ") { &manifest }
                    else if request.starts_with("GET /bad ") { b"tampered archive".as_slice() }
                    else { archive_bytes.as_slice() };
                write!(stream, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len()).unwrap();
                stream.write_all(body).unwrap();
            }
        });
        let target = tempfile::tempdir().unwrap();
        let app_path = target.path().join("CSSwitch Next.app");
        std::fs::create_dir_all(app_path.join("Contents/MacOS")).unwrap();
        std::fs::write(app_path.join("old-build-marker"), "previous version").unwrap();
        let mut context = tauri::test::mock_context(tauri::test::noop_assets());
        let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        context.config_mut().plugins = serde_json::from_value(config["plugins"].clone()).unwrap();
        let app = tauri::test::mock_builder()
            .plugin(tauri_plugin_updater::Builder::new().build())
            .build(context).unwrap();
        tauri::async_runtime::block_on(async {
            let update = app.updater_builder().no_proxy()
                .endpoints(vec![format!("{base}/manifest").parse().unwrap()]).unwrap()
                .executable_path(app_path.join("Contents/MacOS/desktop"))
                .version_comparator(|_, _| true) // Allow same version only in this test.
                .build().unwrap().check().await.unwrap().unwrap();
            let downloaded = update.download(|_, _| {}, || {}).await.unwrap();
            assert_eq!(downloaded, bytes);
            let mut corrupted = update.clone();
            corrupted.download_url = format!("{base}/bad").parse().unwrap();
            assert!(corrupted.download(|_, _| {}, || {}).await.is_err());
            let mut mismatched = update.clone();
            mismatched.version = "9.9.9".into();
            assert!(mismatched.download(|_, _| {}, || {}).await.is_err());
            update.install(downloaded).unwrap();
            assert!(!app_path.join("old-build-marker").exists());
            assert!(app_path.join("Contents/Resources/scripts/science-connector-run.py").is_file());
            assert!(std::process::Command::new("codesign").args(["--verify", "--deep", "--strict"])
                .arg(&app_path).status().unwrap().success());
        });
        server.join().unwrap();
    }
}
