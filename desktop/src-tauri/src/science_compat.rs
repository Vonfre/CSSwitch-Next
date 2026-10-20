//! Read-only, bounded checks; never expose command output or credentials to JS.
use serde_json::{json, Value};
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

pub fn inspect(root: &Path) -> Result<Value, String> {
    let python = super::proc::find_exe("python3").ok_or("未找到 Python 3，请安装后重新检查。")?;
    let input = json!({"home": super::science_shared::home()?,
        "binary": super::science_bin(), "node": super::proc::find_exe("node")});
    let mut child = Command::new(python)
        .arg("-I").arg("-B").arg(root.join("scripts/science-compatibility.py"))
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
        .spawn().map_err(|_| "无法启动兼容性检查，请检查 Python 环境。")?;
    if child.stdin.take().unwrap().write_all(input.to_string().as_bytes()).is_err() {
        let _ = child.kill(); let _ = child.wait();
        return Err("无法发送检查参数。".into());
    }
    let result = child.wait_with_output().map_err(|_| "兼容性检查未完成。")?;
    if !result.status.success() { return Err("兼容性检查未完成，请检查 Python 运行环境并重试。".into()); }
    serde_json::from_slice(&result.stdout).map_err(|_| "兼容性检查返回异常，请重试。".into())
}
