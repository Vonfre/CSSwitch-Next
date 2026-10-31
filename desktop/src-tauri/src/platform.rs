//! OS boundaries shared by the desktop and its subprocesses.
use std::{ffi::OsStr, fs, io, path::{Path, PathBuf}, process::Command};

pub fn home() -> Option<PathBuf> {
    #[cfg(windows)]
    let value = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME"));
    #[cfg(not(windows))]
    let value = std::env::var_os("HOME");
    value.map(PathBuf::from).filter(|p| p.is_absolute())
}

pub fn command(program: impl AsRef<OsStr>) -> Command {
    let mut command = Command::new(program);
    #[cfg(windows)] {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    command.env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8");
    if Path::new(command.get_program()).file_stem().is_some_and(|s| s.to_string_lossy().starts_with("python")) {
        command.args(["-X", "utf8"]);
    }
    command
}

pub fn random(bytes: &mut [u8]) -> io::Result<()> {
    #[cfg(windows)] {
        #[link(name = "bcrypt")]
        extern "system" { fn BCryptGenRandom(handle: *mut std::ffi::c_void, buffer: *mut u8, len: u32, flags: u32) -> i32; }
        let result = unsafe { BCryptGenRandom(std::ptr::null_mut(), bytes.as_mut_ptr(), bytes.len() as u32, 2) };
        if result < 0 { return Err(io::Error::other("Windows random source failed")); }
        Ok(())
    }
    #[cfg(not(windows))] {
        use std::io::Read;
        fs::File::open("/dev/urandom")?.read_exact(bytes)
    }
}

pub fn executable(path: &Path) -> bool {
    let Ok(metadata) = fs::symlink_metadata(path) else { return false };
    if !metadata.is_file() { return false; }
    #[cfg(unix)] {
        use std::os::unix::fs::PermissionsExt;
        metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(windows)] { true }
}

/// On Windows reject all reparse points, including directory junctions.
pub fn is_link(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)] {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))] { metadata.file_type().is_symlink() }
}

pub trait PrivateOpenOptions {
    fn mode(&mut self, mode: u32) -> &mut Self;
    fn nofollow(&mut self) -> &mut Self;
}
impl PrivateOpenOptions for fs::OpenOptions {
    fn mode(&mut self, mode: u32) -> &mut Self {
        #[cfg(unix)] { std::os::unix::fs::OpenOptionsExt::mode(self, mode); }
        #[cfg(windows)] { let _ = mode; } // Restricted parent DACL is inherited.
        self
    }
    fn nofollow(&mut self) -> &mut Self {
        #[cfg(unix)] { std::os::unix::fs::OpenOptionsExt::custom_flags(self, super::libc_o_nofollow()); }
        #[cfg(windows)] { std::os::windows::fs::OpenOptionsExt::custom_flags(self, 0x00200000); }
        self
    }
}

/// Protect secrets using Unix modes or a protected Windows owner/SYSTEM DACL.
pub fn permissions(path: &Path, mode: u32) -> io::Result<()> {
    #[cfg(unix)] {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(mode))
    }
    #[cfg(windows)] {
        use std::os::windows::ffi::OsStrExt;
        type Pointer = *mut std::ffi::c_void;
        #[link(name = "advapi32")]
        extern "system" {
            fn ConvertStringSecurityDescriptorToSecurityDescriptorW(text: *const u16, revision: u32, descriptor: *mut Pointer, size: *mut u32) -> i32;
            fn SetFileSecurityW(path: *const u16, information: u32, descriptor: Pointer) -> i32;
        }
        #[link(name = "kernel32")]
        extern "system" { fn LocalFree(memory: Pointer) -> Pointer; }
        let _ = mode;
        if fs::symlink_metadata(path).is_ok_and(|m| is_link(&m)) {
            return Err(io::Error::other("Refusing permissions on a reparse point"));
        }
        let text: Vec<u16> = "D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)\0".encode_utf16().collect();
        let name: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut descriptor = std::ptr::null_mut();
        unsafe {
            if ConvertStringSecurityDescriptorToSecurityDescriptorW(text.as_ptr(), 1, &mut descriptor, std::ptr::null_mut()) == 0 {
                return Err(io::Error::last_os_error());
            }
            let ok = SetFileSecurityW(name.as_ptr(), 0x80000004, descriptor);
            let result = if ok == 0 { Err(io::Error::last_os_error()) } else { Ok(()) };
            LocalFree(descriptor);
            result
        }
    }
}

pub fn open(target: &OsStr) -> Result<(), String> {
    tauri_plugin_opener::open_path(Path::new(target), None::<&str>).map_err(|e| e.to_string())
}

pub fn science_binary() -> PathBuf {
    if let Some(path) = std::env::var_os("CSSWITCH_SCIENCE_BIN") { return path.into(); }
    #[cfg(windows)] {
        let home = home().unwrap_or_default();
        let local = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(|| home.join("AppData/Local"));
        let paths = [home.join(".claude-science/bin/claude-science.exe"), local.join("Programs/ClaudeScience/claude-science.exe"), local.join("ClaudeScience/claude-science.exe")];
        paths.iter().find(|p| p.is_file()).cloned().unwrap_or_else(|| paths[1].clone())
    }
    #[cfg(not(windows))] {
        super::science_shared::preferred_binary(home().as_deref(), Path::new("/Applications/Claude Science.app/Contents/Resources/bin/claude-science"))
    }
}

/// Recover only a gateway from this exact installation/configuration/port.
#[cfg(windows)]
pub fn cleanup_proxy(script: &Path, config: &Path, port: u16) -> Result<(), String> {
    if std::net::TcpListener::bind(("127.0.0.1", port)).is_ok() { return Ok(()); }
    let output = command("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", r#"
$ErrorActionPreference='Stop'
$scriptPattern=[regex]::Escape($env:CSSWITCH_ORPHAN_SCRIPT)
$configPattern=[regex]::Escape($env:CSSWITCH_ORPHAN_CONFIG)
$portPattern='--port\s+' + [regex]::Escape($env:CSSWITCH_ORPHAN_PORT) + '(\s|$)'
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='python3.exe'" |
  Where-Object { $_.CommandLine -match $scriptPattern -and $_.CommandLine -match $configPattern -and $_.CommandLine -match $portPattern } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction Stop }
"#])
        .env("CSSWITCH_ORPHAN_SCRIPT", script)
        .env("CSSWITCH_ORPHAN_CONFIG", config)
        .env("CSSWITCH_ORPHAN_PORT", port.to_string())
        .output().map_err(|_| "端口被占用，无法检查上次遗留的 CSSwitch 网关。")?;
    if !output.status.success() { return Err("端口被占用，无法确认遗留进程归属。请退出旧 CSSwitch 或更改网关端口。".into()); }
    Ok(())
}
