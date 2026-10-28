"""Memory locking and pre-allocated mitigation path (GV-8)."""
import ctypes
import os
import resource
from pathlib import Path

MCL_CURRENT, MCL_FUTURE = 1, 2


def lock_memory() -> None:
    """mlockall(MCL_CURRENT | MCL_FUTURE); raises OSError (e.g. ENOMEM when RLIMIT_MEMLOCK is low)."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.mlockall(MCL_CURRENT | MCL_FUTURE) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def locked_kb() -> int:
    """VmLck of this process in kB."""
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmLck:"):
            return int(line.split()[1])
    return 0


def raise_memlock_limit() -> None:
    """Best effort: lift the soft RLIMIT_MEMLOCK to the hard limit."""
    _, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    resource.setrlimit(resource.RLIMIT_MEMLOCK, (hard, hard))


class MitigationPath:
    """File descriptors and buffers opened before pressure hits, so L3/L4 needs no allocation."""

    def __init__(self, buf_size: int = 64 * 1024):
        self.buf = bytearray(buf_size)  # touched below so the pages are resident
        self.buf[:] = b"\0" * buf_size
        self._fds: dict[str, int] = {}

    def open(self, path: str, flags: int = os.O_WRONLY) -> int:
        if path not in self._fds:
            self._fds[path] = os.open(path, flags)
        return self._fds[path]

    def write(self, path: str, data: bytes) -> None:
        """Write to a pre-opened handle; opening here would defeat the purpose."""
        os.write(self._fds[path], data)

    def close(self) -> None:
        for fd in self._fds.values():
            os.close(fd)
        self._fds.clear()
