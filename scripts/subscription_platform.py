"""Windows credential protection and process adapters; POSIX remains unchanged."""
import ctypes
import importlib.util
import os
from pathlib import Path
import subprocess
import json
from csswitch_platform import lock_file

WINDOWS = os.name == 'nt'


def linked(path):
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
    except FileNotFoundError:
        return False


def open_read(path):
    if not WINDOWS:
        return os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    import msvcrt
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle(handle)
        raise


def permissions():
    spec = importlib.util.spec_from_file_location('subscription_private_files', Path(__file__).resolve().parents[1] / 'proxy/private_files.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def protect(path):
    if WINDOWS: permissions().protect_windows(path)
    else: os.chmod(path, 0o700 if Path(path).is_dir() else 0o600)


def private_handle(fd):
    return permissions().windows_private(fd)


def dpapi(data, decrypt=False):
    from ctypes import wintypes as w
    class Blob(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.POINTER(Blob)]
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try: return ctypes.string_at(target.data, target.size)
    finally:
        kernel = ctypes.WinDLL('kernel32'); kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(target.data)


def subprocess_options():
    return {'creationflags': 0x08000000} if WINDOWS else {}


def close_science_windows(home, runner):
    """Ask only the installed Science Electron windows to close; never kill them."""
    root = Path(home) / '.claude-science/electron'
    if not root.is_dir():
        return False
    # Pass paths as data over stdin, never interpolate them into PowerShell code.
    script = r'''
$ErrorActionPreference='Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$root = [IO.Path]::GetFullPath([Console]::In.ReadToEnd()).TrimEnd('\') + '\'
$found = $false
Get-Process -Name 'claude-science' -ErrorAction SilentlyContinue | ForEach-Object {
    $p = $_
    if ($p.Path -and $p.Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -and $p.MainWindowHandle -ne 0) {
        $found = $true
        if (-not $p.CloseMainWindow() -or -not $p.WaitForExit(10000)) { throw 'Science window is still open' }
    }
}
if ($found) { 'true' } else { 'false' }
'''
    result = runner(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                    input=str(root), capture_output=True, text=True, encoding='utf-8',
                    timeout=30, **subprocess_options())
    if result.returncode:
        raise RuntimeError('Science window is still open')
    return json.loads(result.stdout.strip())
