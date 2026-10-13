//! Offline, read-only Science history and local skill discovery.
use serde_json::{json, Value};
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::Mutex;

static OPERATION: Mutex<()> = Mutex::new(());

pub fn manage(root: &Path, operation: &str, params: Value) -> Result<Value, String> {
    let _guard = OPERATION.lock().unwrap_or_else(|e| e.into_inner());
    let home = super::science_shared::home()?;
    let (data, _) = super::science_shared::local_settings(&home)?;
    let python = super::proc::find_exe("python3").ok_or("本地管理需要 Python 3。")?;
    let mut child = Command::new(python)
        .arg("-I")
        .arg(root.join("scripts/science-workspace.py"))
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
        .spawn().map_err(|_| "无法启动本地管理组件。")?;
    let bytes = serde_json::to_vec(&json!({ "home": home, "data": data,
        "config": super::config::default_dir(), "operation": operation, "params": params }))
        .map_err(|_| "本地管理参数无效。")?;
    if child.stdin.take().unwrap().write_all(&bytes).is_err() {
        let _ = child.kill(); let _ = child.wait();
        return Err("无法发送本地管理请求。".into());
    }
    let output = child.wait_with_output().map_err(|_| "本地管理组件未完成。")?;
    let result: Value = serde_json::from_slice(&output.stdout).map_err(|_| "本地管理组件返回异常或超时。")?;
    if result.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(result.get("message").and_then(Value::as_str).unwrap_or("本地管理失败。").into());
    }
    Ok(result.get("value").cloned().unwrap_or(json!({})))
}
