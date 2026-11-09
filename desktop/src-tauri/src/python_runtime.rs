//! All desktop Python bridges share one validated interpreter, preferring the shipped runtime.
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

const PROBE: &str = "import sys,ssl,sqlite3,tomllib; assert sys.version_info >= (3,11); from cryptography.fernet import Fernet; from cryptography.hazmat.primitives.ciphers.aead import AESGCM; f=Fernet(Fernet.generate_key()); assert f.decrypt(f.encrypt(b'probe'))==b'probe'; a=AESGCM(bytes(32)); assert a.decrypt(bytes(12),a.encrypt(bytes(12),b'probe',None),None)==b'probe'; assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0";

pub fn compatible(path: &Path, timeout: Duration) -> bool {
    let Ok(resolved) = path.canonicalize() else { return false };
    let path = resolved.as_path();
    if !crate::platform::executable(path) { return false; }
    let mut child = match crate::platform::command(path).args(["-I", "-c", PROBE])
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn() {
        Ok(child) => child, Err(_) => return false,
    };
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(20)),
            _ => { let _ = child.kill(); let _ = child.wait(); return false; }
        }
    }
}

fn first_compatible(candidates: Vec<PathBuf>, timeout: Duration) -> Option<PathBuf> {
    let mut seen = std::collections::HashSet::new();
    candidates.into_iter().filter_map(|p| p.canonicalize().ok()).find(|p| seen.insert(p.clone()) && compatible(p, timeout))
}

fn candidates() -> Vec<PathBuf> {
    let mut paths = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(bin) = exe.parent() {
            #[cfg(target_os = "macos")]
            if let Some(contents) = bin.parent().filter(|p| p.file_name().is_some_and(|n| n == "Contents")) {
                paths.push(contents.join("Resources/runtime/python/bin/python3"));
            }
            #[cfg(windows)]
            paths.push(bin.join("runtime/python/python.exe"));
        }
    }
    // Developer builds use the same prepared runtime; release apps never depend on a checkout.
    #[cfg(debug_assertions)] {
        let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        #[cfg(target_os = "macos")]
        paths.push(repo.join(".runtime/macos/python/bin/python3"));
        #[cfg(windows)]
        paths.push(repo.join(".runtime/python/python.exe"));
    }
    if let Some(home) = crate::platform::home() {
        paths.push(home.join(if cfg!(windows) { ".claude-science/conda/envs/claude-science-mcp/python.exe" } else { ".claude-science/conda/envs/claude-science-mcp/bin/python3" }));
    }
    #[cfg(not(windows))]
    for path in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/opt/local/bin/python3"] { paths.push(path.into()); }
    if let Some(path) = crate::proc::which("python3") { paths.push(path); }
    #[cfg(windows)]
    if let Some(path) = crate::proc::which("python") { paths.push(path); }
    paths
}

pub fn find() -> Option<PathBuf> {
    static VALIDATED: OnceLock<Option<PathBuf>> = OnceLock::new();
    VALIDATED.get_or_init(|| {
        first_compatible(candidates(), Duration::from_secs(5)).or_else(|| {
            #[cfg(not(windows))] {
                crate::proc::which_via_login_shell("python3")
                    .filter(|p| compatible(p, Duration::from_secs(5)))
            }
            #[cfg(windows)] { None }
        })
    }).clone()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(unix)]
    #[test]
    fn rejects_missing_broken_and_hung_runtimes_and_falls_back() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("csswitch-python-probe-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        for (name, content) in [("broken", "#!/bin/sh\nexit 1\n"), ("hung", "#!/bin/sh\nexec /bin/sleep 30\n"), ("valid", "#!/bin/sh\nexit 0\n")] {
            let path=dir.join(name); std::fs::write(&path,content).unwrap();
            std::fs::set_permissions(path,std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        std::os::unix::fs::symlink(dir.join("valid"),dir.join("python3")).unwrap();
        assert!(compatible(&dir.join("python3"),Duration::from_millis(750)));
        let start=Instant::now();
        assert_eq!(first_compatible(vec![dir.join("missing"),dir.join("broken"),dir.join("hung"),dir.join("valid")],Duration::from_millis(750)),Some(dir.join("valid").canonicalize().unwrap()));
        assert!(start.elapsed()<Duration::from_secs(5));
        std::fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn selected_runtime_imports_and_executes_required_modules() {
        let python=find().expect("prepare the bundled runtime before testing");
        assert!(compatible(&python,Duration::from_secs(5)));
    }
}
