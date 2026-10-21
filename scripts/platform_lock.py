"""Nonblocking process locks using only the current platform's standard library.

Lock files are stable sidecars: never delete or truncate them. Windows locks the
same byte at offset zero, even for an empty file; POSIX locks the whole file.
The OS releases the lock when the descriptor closes, including process exit.
"""
from contextlib import contextmanager
import errno
import os
from pathlib import Path

_PLATFORM = os.name


def _backend():
    if _PLATFORM == "nt":
        import msvcrt
        return "windows", msvcrt
    if _PLATFORM == "posix":
        import fcntl
        return "posix", fcntl
    raise OSError(f"unsupported file-lock platform: {_PLATFORM}")


def _contention(error):
    return (error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
            or getattr(error, "winerror", None) in {32, 33, 36})


@contextmanager
def nonblocking_file_lock(path, *, busy_message):
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"lock file must not be a symlink: {path}")
    kind, api = _backend()
    # Unbuffered binary I/O avoids text/newline and buffered-position ambiguity.
    # No contents are written: msvcrt permits a lock range past EOF.
    with path.open("a+b", buffering=0) as stream:
        descriptor = stream.fileno()
        try:
            if kind == "windows":
                stream.seek(0)
                api.locking(descriptor, api.LK_NBLCK, 1)
            else:
                api.flock(descriptor, api.LOCK_EX | api.LOCK_NB)
        except OSError as error:
            if _contention(error):
                raise ValueError(busy_message) from error
            raise
        try:
            yield
        finally:
            if kind == "windows":
                stream.seek(0)
                api.locking(descriptor, api.LK_UNLCK, 1)
            else:
                api.flock(descriptor, api.LOCK_UN)
