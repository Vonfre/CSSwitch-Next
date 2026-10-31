"""Validate the NTFS DACL of an already-open configuration file."""
import os


def windows_private(fd):
    import ctypes as c
    from ctypes import wintypes as w
    import msvcrt
    adv = c.WinDLL('advapi32', use_last_error=True)
    kernel = c.WinDLL('kernel32', use_last_error=True)
    pointer = c.c_void_p
    adv.GetSecurityInfo.argtypes = [w.HANDLE, w.DWORD, w.DWORD, c.POINTER(pointer), pointer, c.POINTER(pointer), pointer, c.POINTER(pointer)]
    adv.GetSecurityInfo.restype = w.DWORD
    adv.GetAce.argtypes = [pointer, w.DWORD, c.POINTER(pointer)]
    adv.EqualSid.argtypes = [pointer, pointer]
    adv.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, c.POINTER(pointer)]
    kernel.LocalFree.argtypes = [pointer]
    class ACL(c.Structure):
        _fields_ = [('revision', c.c_ubyte), ('reserved', c.c_ubyte), ('size', w.WORD), ('count', w.WORD), ('reserved2', w.WORD)]
    owner, acl, descriptor = pointer(), pointer(), pointer()
    if adv.GetSecurityInfo(msvcrt.get_osfhandle(fd), 1, 5, c.byref(owner), None, c.byref(acl), None, c.byref(descriptor)):
        return False
    known=[]
    try:
        if not acl: return False  # NULL DACL grants everyone full access.
        for text in ('S-1-5-18', 'S-1-5-32-544', 'S-1-3-4'):
            sid=pointer()
            if not adv.ConvertStringSidToSidW(text, c.byref(sid)): return False
            known.append(sid)
        for i in range(c.cast(acl, c.POINTER(ACL)).contents.count):
            ace=pointer()
            if not adv.GetAce(acl, i, c.byref(ace)): return False
            kind=c.c_ubyte.from_address(ace.value).value
            if kind == 1: continue  # Deny ACE cannot broaden access.
            if kind != 0: return False  # Fail closed on an unknown ACE layout.
            mask=w.DWORD.from_address(ace.value+4).value
            sid=pointer(ace.value+8)
            if mask & 0x90000001 and not any(adv.EqualSid(sid, allowed) for allowed in [owner, *known]):
                return False
        return True
    finally:
        for sid in known: kernel.LocalFree(sid)
        kernel.LocalFree(descriptor)


def protect_windows(path):
    """Apply the same owner/SYSTEM DACL as the desktop (also used by tests)."""
    import ctypes as c
    from ctypes import wintypes as w
    adv=c.WinDLL('advapi32', use_last_error=True)
    kernel=c.WinDLL('kernel32', use_last_error=True)
    pointer=c.c_void_p
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,c.POINTER(pointer),pointer]
    adv.SetFileSecurityW.argtypes=[w.LPCWSTR,w.DWORD,pointer]
    kernel.LocalFree.argtypes=[pointer]
    descriptor=pointer()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)',1,c.byref(descriptor),None):
        raise c.WinError(c.get_last_error())
    try:
        if not adv.SetFileSecurityW(os.fspath(path),0x80000004,descriptor): raise c.WinError(c.get_last_error())
    finally:
        kernel.LocalFree(descriptor)
