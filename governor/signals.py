"""Host memory signals read from /proc (GV-2)."""
from dataclasses import dataclass
from pathlib import Path

GB = 1024**3


@dataclass(frozen=True)
class Signals:
    mem_available: int  # bytes
    psi_some_avg10: float  # percent
    psi_full_avg10: float  # percent
    swap_out_rate: float = 0.0  # pages/s, filled by Sampler


def parse_psi(text: str) -> dict[str, float]:
    """Parse /proc/pressure/memory into {'some': avg10, 'full': avg10}."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        kind, *fields = line.split()
        kv = dict(f.split("=") for f in fields)
        out[kind] = float(kv["avg10"])
    return out


def parse_meminfo(text: str) -> dict[str, int]:
    """Parse /proc/meminfo into bytes."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            out[key] = int(parts[0]) * (1024 if len(parts) > 1 else 1)
    return out


def parse_pswpout(text: str) -> int:
    for line in text.splitlines():
        if line.startswith("pswpout "):
            return int(line.split()[1])
    return 0


class Sampler:
    def __init__(self, proc: Path = Path("/proc")):
        self.proc = proc
        self._last: tuple[float, int] | None = None

    def sample(self, now: float) -> Signals:
        mem = parse_meminfo((self.proc / "meminfo").read_text())
        psi = parse_psi((self.proc / "pressure/memory").read_text())
        pswp = parse_pswpout((self.proc / "vmstat").read_text())
        rate = 0.0
        if self._last and now > self._last[0]:
            rate = (pswp - self._last[1]) / (now - self._last[0])
        self._last = (now, pswp)
        return Signals(mem["MemAvailable"], psi["some"], psi.get("full", 0.0), rate)
