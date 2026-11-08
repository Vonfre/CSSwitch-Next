//! CSSwitch's independent subscription service. Only public account status crosses IPC.
use serde_json::{json, Value};
use std::io::Write;
use std::process::Stdio;

pub fn manage(root: &std::path::Path, operation: &str, params: Value) -> Result<Value, String> {
    if !["status", "auto", "select", "switch", "recover", "connect", "login", "start"].contains(&operation) {
        return Err("未知订阅管理操作。".into());
    }
    let native_python = if cfg!(windows) { None } else { super::platform::home()
        .map(|p| p.join(".claude-science/conda/envs/claude-science-mcp/bin/python3")) };
    let python = native_python.filter(|p| p.is_file()).or_else(|| super::proc::find_exe("python3"))
        .ok_or("订阅管理需要 Python 3 和 cryptography。")?;
    let mut child = super::platform::command(python)
        .arg("-I").arg(root.join("scripts/science-subscriptions.py"))
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
        .spawn().map_err(|_| "无法启动订阅管理组件。")?;
    let bytes = serde_json::to_vec(&json!({"operation": operation, "params": params}))
        .map_err(|_| "订阅管理参数无效。")?;
    if child.stdin.take().unwrap().write_all(&bytes).is_err() {
        let _ = child.kill(); let _ = child.wait();
        return Err("无法发送订阅管理请求。".into());
    }
    let output = child.wait_with_output().map_err(|_| "订阅管理组件未完成。")?;
    let result: Value = serde_json::from_slice(&output.stdout).map_err(|_| "订阅管理返回异常，请刷新检查。")?;
    if result.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(result.get("message").and_then(Value::as_str).unwrap_or("订阅管理失败。").into());
    }
    Ok(result.get("value").cloned().unwrap_or(json!({})))
}
