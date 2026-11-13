"""Kernel PSI trigger (GV-2, issue 464): wake the control loop when memory stalls, instead of waiting out the interval.

`some 150000 2000000` = at least 150 ms of stall inside a 2 s window. The window is 2 s because an unprivileged
process may only create triggers whose window is a multiple of 2 s (checked on kernel 7.0 as a normal user: 1 s is
refused with EINVAL, 2 s works). Where the file cannot be opened or written (an old kernel, PSI off, a refusal) the
trigger stays inactive and `wait` is a plain sleep: the governor then polls exactly as before.
"""
import os
import select
import time

PATH = "/proc/pressure/memory"
SPEC = b"some 150000 2000000\0"


class PsiTrigger:
    def __init__(self, path: str = PATH, spec: bytes = SPEC):
        self.fd: int | None = None
        self.why = ""  # why the trigger is not active, for the log
        self._poll = select.poll()
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as e:
            self.why = f"cannot open {path}: {e.strerror}"
            return
        try:
            os.write(fd, spec)
        except OSError as e:
            os.close(fd)
            self.why = f"the kernel refused the trigger: {e.strerror}"
            return
        self.fd = fd
        self._poll.register(fd, select.POLLPRI)

    @property
    def active(self) -> bool:
        return self.fd is not None

    def wait(self, seconds: float) -> bool:
        """Sleep up to `seconds`; True when the trigger fired and cut the wait short."""
        if self.fd is None:
            time.sleep(seconds)
            return False
        return bool(self._poll.poll(seconds * 1000))

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
