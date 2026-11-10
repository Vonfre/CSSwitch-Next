//! Credential-free environment monitor, independent of Science and quota locks.
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::path::Path;
use std::process::Stdio;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::Emitter;

static OPERATION: Mutex<()> = Mutex::new(());

#[tauri::command]
pub fn environment_open_source(source: String) -> Result<(), String> {
    let url = match source.as_str() {
        "regions" => "https://support.claude.com/en/articles/8461763-where-can-i-access-claude",
        "reference" => "https://ip.net.coffee/claude/",
        "dns" => "https://ip.net.coffee/dns/",
        "webrtc" => "https://ip.net.coffee/webrtc/",
        _ => return Err("未知检测来源。".into()),
    };
    crate::open_in_browser(url)
}

fn manage(root: &Path, operation: &str, enabled: Option<bool>, ips: Option<Vec<String>>) -> Result<Value, String> {
    if !["status", "tick", "refresh", "configure", "clear", "dns", "udp"].contains(&operation) {
        return Err("未知环境检测操作。".into());
    }
    // Reads use the atomic cache; they never wait for a network scan or trigger one.
    let _guard = if operation == "status" { None } else {
        Some(OPERATION.lock().unwrap_or_else(|e| e.into_inner()))
    };
    let python = crate::proc::find_exe("python3").ok_or("内置环境检测组件不可用，请重新安装完整应用。")?;
    let mut child = crate::platform::command(python)
        .arg("-I").arg(root.join("scripts/environment_monitor.py"))
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
        .spawn().map_err(|_| "无法启动环境检测。")?;
    let ips = ips.unwrap_or_default().into_iter().take(3).map(|ip| ip.chars().take(64).collect::<String>()).collect::<Vec<_>>();
    let input = serde_json::to_vec(&json!({"operation": operation, "enabled": enabled, "ips": ips})).unwrap();
    if child.stdin.take().unwrap().write_all(&input).is_err() {
        let _ = child.kill(); let _ = child.wait();
        return Err("无法发送环境检测请求。".into());
    }
    // Drain stdout while waiting, with both an output cap and a process deadline.
    let stdout = child.stdout.take().unwrap();
    let reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout.take(262145).read_to_end(&mut bytes).map(|_| bytes)
    });
    let deadline = Instant::now() + Duration::from_secs(48);
    let success = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status.success(),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(50)),
            _ => { let _ = child.kill(); let _ = child.wait(); break false; }
        }
    };
    let output = reader.join().map_err(|_| "环境检测返回异常。")?.map_err(|_| "环境检测返回异常。")?;
    if !success || output.len() > 262144 { return Err("环境检测超时或未完成，请稍后重试。".into()); }
    let result: Value = serde_json::from_slice(&output).map_err(|_| "环境检测返回异常。")?;
    if result.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err("环境检测未完成，请稍后重试或检查本地检测记录。".into());
    }
    Ok(result.get("value").cloned().unwrap_or(json!({})))
}

#[tauri::command]
pub async fn environment_manage(app: tauri::AppHandle, operation: String, enabled: Option<bool>, ips: Option<Vec<String>>) -> Result<Value, String> {
    if !["status", "refresh", "configure", "clear", "dns", "udp"].contains(&operation.as_str()) {
        return Err("未知环境检测操作。".into());
    }
    let root = crate::asset_root(&app).ok_or("无法定位环境检测资源。")?;
    let emit = operation != "status";
    let result = tauri::async_runtime::spawn_blocking(move || manage(&root, &operation, enabled, ips))
        .await.map_err(|_| "环境检测任务异常。".to_string())??;
    if emit { let _ = app.emit("environment-updated", &result); }
    Ok(result)
}
