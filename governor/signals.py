"""Host memory signals read from /proc (GV-2)."""
import os
from dataclasses import dataclass
from pathlib import Path

GB = 1024**3


@dataclass(frozen=True)
class Signals:
    mem_available: int  # bytes
    psi_some_avg10: float  # percent
    psi_full_avg10: float  # percent
    swap_out_rate: float = 0.0  # pages/s, filled by Sampler
    compressed_swap: int = 0  # bytes of RAM held by zswap and zram pools (already taken off mem_available, #463)


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


def parse_memory_events(text: str) -> dict[str, int]:
    """Parse a cgroup's memory.events ('high 3', 'oom_kill 1', ...)."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        k, _, v = line.partition(" ")
        if v.strip().isdigit():
            out[k] = int(v)
    return out


class _Reader:
    """A file opened once, read into a buffer made once (GV-8: no open, no new buffer on the signal path)."""

    def __init__(self, path: Path, size: int = 16384):
        self.fd = os.open(path, os.O_RDONLY)
        self.buf = bytearray(size)

    def read(self) -> str:
        n = os.preadv(self.fd, [self.buf], 0)
        return self.buf[:n].decode()

    def close(self) -> None:
        os.close(self.fd)


class Sampler:
    """/proc signals through pre-opened descriptors. Parsing still makes small strings (Python), but nothing is opened,
    globbed or sized per tick; zram devices are looked for again every ZRAM_RESCAN samples."""

    ZRAM_RESCAN = 60

    def __init__(self, proc: Path = Path("/proc"), sysblock: Path = Path("/sys/block")):
        self.proc, self.sysblock = proc, sysblock
        self._last: tuple[float, int] | None = None
        self._meminfo = _Reader(proc / "meminfo")
        self._psi = _Reader(proc / "pressure/memory", 512)
        self._vmstat = _Reader(proc / "vmstat", 32768)
        self._zram: dict[Path, _Reader] = {}
        self._ticks = 0

    def _scan_zram(self) -> None:
        for f in self.sysblock.glob("zram*/mm_stat"):
            if f not in self._zram:
                try:
                    self._zram[f] = _Reader(f, 256)
                except OSError:
                    continue

    def _zram_bytes(self) -> int:
        total = 0
        for r in self._zram.values():
            try:
                total += int(r.read().split()[2])
            except (OSError, ValueError, IndexError):
                continue
        return total

    def close(self) -> None:
        for r in (self._meminfo, self._psi, self._vmstat, *self._zram.values()):
            r.close()

    def sample(self, now: float) -> Signals:
        if self._ticks % self.ZRAM_RESCAN == 0:
            self._scan_zram()
        self._ticks += 1
        mem = parse_meminfo(self._meminfo.read())
        psi = parse_psi(self._psi.read())
        pswp = parse_pswpout(self._vmstat.read())
        rate = 0.0
        if self._last and now > self._last[0]:
            rate = (pswp - self._last[1]) / (now - self._last[0])
        self._last = (now, pswp)
        # MemAvailable counts pages that compression would only shrink, not free: with a compressed swap in use the
        # headroom is overstated by what the pools already hold, so take that off (conservative, #463)
        pools = mem.get("Zswap", 0) + self._zram_bytes()
        return Signals(max(mem["MemAvailable"] - pools, 0), psi["some"], psi.get("full", 0.0), rate, pools)


class FileSampler:
    """Reads signals from a JSON file ({"mem_available", "psi_some", "psi_full"}).

    For tests and threshold tuning: lets a scenario drive the ladder without real pressure.
    """

    def __init__(self, path: Path):
        self.path = path

    def sample(self, now: float) -> Signals:
        import json

        d = json.loads(self.path.read_text())
        return Signals(int(d["mem_available"]), float(d["psi_some"]), float(d["psi_full"]),
                       float(d.get("swap_out_rate", 0.0)), int(d.get("compressed_swap", 0)))


class CgroupSampler:
    """Signals scoped to one cgroup: PSI from its memory.pressure, headroom = memory.max - current.

    Lets the ladder react to a single capped group (per-cgroup PSI, GV-2) and makes a safe
    real-pressure test possible without squeezing the whole host.
    """

    def __init__(self, cg: Path):
        self.cg = cg

    def sample(self, now: float) -> Signals:
        try:
            psi = parse_psi((self.cg / "memory.pressure").read_text())
            cap = (self.cg / "memory.max").read_text().strip()
            cur = int((self.cg / "memory.current").read_text())
        except FileNotFoundError:  # the watched container was stopped and its cgroup removed: calm, not a crash
            return Signals(1 << 40, 0.0, 0.0)
        headroom = max(int(cap) - cur, 0) if cap != "max" else 1 << 40
        return Signals(headroom, psi["some"], psi.get("full", 0.0))
