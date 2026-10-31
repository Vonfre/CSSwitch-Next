//! Operation-specific bridge to an owned Science session. Local cookies stay native.
use serde_json::{json, Value};
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

pub fn manage(root: &Path, operation: &str, mut params: Value) -> Result<Value, String> {
    if ![
        "list",
        "add_local",
        "add_remote",
        "install",
        "export_science",
        "attach",
        "detach",
        "enabled",
        "remove",
        "tools",
        "grant",
        "authorize",
        "skills_list",
        "skills_content",
        "skills_enabled",
        "skills_import",
        "skills_update",
        "skills_remove",
    ]
    .contains(&operation)
    {
        return Err("不支持的 MCP 管理操作。".into());
    }
    if operation == "add_local" {
        if let Some(command) = params.get("command").and_then(Value::as_str) {
            if !Path::new(command).is_absolute() {
                let executable = super::proc::find_exe(command).ok_or("找不到 MCP 可执行文件，请安装或填写绝对路径。")?;
                params["command"] = json!(executable);
            }
        }
    }
    let session = super::science_shared::load(&super::shared_root())?
        .ok_or("请先在提供商页面启动第三方 Science。")?;
    let bin = super::science_bin();
    if !super::science_shared::owns_status(
        &super::science_shared::status(&bin, &session)?,
        &session,
    ) {
        return Err("请先在提供商页面启动 CSSwitch 所管理的第三方 Science。".into());
    }
    if operation == "install" || operation == "export_science" {
        let cfg = super::config::load_from(&super::config::default_dir())
            .map_err(|_| "无法读取网关端口。")?;
        let script = root.join("scripts/science-connector-run.py");
        if !script.is_file() { return Err("缺少本机连接器资源，请重新安装 CSSwitch。".into()); }
        params["home"] = json!(session.home);
        params["launcher"] = json!(script);
        params["proxy_port"] = json!(cfg.proxy_port);
        params["node"] = json!(super::proc::find_exe("node"));
    }
    // Generate a fresh nonce for this exact owned instance; it never reaches JS.
    let output = super::science_shared::command(&bin, &session, "url")
        .output()
        .map_err(|_| "无法获取 Science 本地会话。")?;
    if !output.status.success() {
        return Err("无法获取 Science 本地会话。".into());
    }
    let link = super::first_http_url(&String::from_utf8_lossy(&output.stdout))
        .ok_or("Science 未返回本地会话地址。")?;
    let url = url::Url::parse(&link).map_err(|_| "Science 本地地址无效。")?;
    if url.scheme() != "http"
        || !matches!(url.host_str(), Some("127.0.0.1" | "localhost"))
        || url.port() != Some(session.port)
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err("Science 返回的地址不属于 CSSwitch 当前会话。".into());
    }
    let nonce = url
        .query_pairs()
        .find(|(k, _)| k == "nonce")
        .map(|(_, v)| v.into_owned())
        .ok_or("Science 地址缺少本地会话凭据。")?;
    let python = super::proc::find_exe("python3").ok_or("MCP 管理需要 Python 3。")?;
    let mut child = crate::platform::command(python)
        .arg(root.join("scripts/science-mcp-manage.py"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "无法启动 MCP 管理组件。")?;
    let input = json!({"base": format!("http://127.0.0.1:{}", session.port), "nonce": nonce,
        "data_dir": session.data_dir, "auth_dir": session.config.parent().unwrap().join("auth"),
        "home": session.home, "operation": operation, "params": params});
    let bytes = serde_json::to_vec(&input).map_err(|_| "MCP 参数无效。")?;
    if let Err(_) = child.stdin.take().unwrap().write_all(&bytes) {
        let _ = child.kill();
        let _ = child.wait();
        return Err("无法发送 MCP 管理请求。".into());
    }
    let result = child
        .wait_with_output()
        .map_err(|_| "MCP 管理组件未完成。")?;
    let data: Value = serde_json::from_slice(&result.stdout)
        .map_err(|_| "MCP 管理请求超时或组件返回异常，请刷新重试。")?;
    if data.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(data
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("MCP 管理失败。")
            .into());
    }
    let mut value = data.get("value").cloned().unwrap_or(json!({}));
    if operation == "list" {
        let active = super::science_ui::running_policy(&session.config).unwrap_or(false);
        value["localMcpActive"] = json!(active);
        if active {
            if let Some(connectors) = value["connectors"].as_array_mut() {
                for c in connectors {
                    if c["source"] == "bundled" && ["pubmed", "biorxiv", "chembl", "clinical-trials"].iter().any(|name| c["name"] == *name) {
                        c["policyDisabled"] = json!(true);
                        c["enabled"] = json!(false);
                        c["connectionStatus"] = json!("disabled");
                    }
                }
            }
        }
    }
    if operation == "authorize" {
        if let Some(link) = value.get("authorizeUrl").and_then(Value::as_str) {
            let url = url::Url::parse(link).map_err(|_| "授权地址无效。")?;
            if url.scheme() != "https"
                || url.host_str().is_none()
                || !url.username().is_empty()
                || url.password().is_some()
            {
                return Err("授权地址不受支持。".into());
            }
            super::open_in_browser(link)?;
        }
        if let Some(object) = value.as_object_mut() {
            object.remove("authorizeUrl");
        }
    }
    Ok(value)
}
