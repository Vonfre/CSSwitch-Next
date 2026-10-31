"""Small platform helpers; no dependency on third-party Python packages."""
import os
import threading
import re
from pathlib import PurePosixPath, PureWindowsPath


def safe_archive_path(name):
    """Reject absolute paths, NTFS streams, devices and Windows name aliases."""
    if not isinstance(name, str) or not name or '\\' in name:
        return False
    path=PurePosixPath(name)
    if path.is_absolute() or PureWindowsPath(name).drive or '..' in path.parts:
        return False
    for part in path.parts:
        if re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((' ', '.')):
            return False
        if re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]',part.split('.')[0],re.I):
            return False
    return True


def lock_file(fd, blocking=True):
    if os.name == 'nt':
        import msvcrt
        if os.fstat(fd).st_size == 0:
            os.write(fd, b'\0')
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if not blocking:
                raise BlockingIOError('File is locked') from error
            raise
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))


def deadline(seconds):
    """Bound CLI bridges even on Windows, which has no SIGALRM."""
    timer = threading.Timer(seconds, lambda: os._exit(124))
    timer.daemon = True
    timer.start()
    return timer
