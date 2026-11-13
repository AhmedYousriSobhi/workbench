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


def zram_bytes(sysblock: Path) -> int:
    """RAM used by every zram device: the third field of mm_stat is mem_used_total."""
    total = 0
    for f in sysblock.glob("zram*/mm_stat"):
        try:
            total += int(f.read_text().split()[2])
        except (OSError, ValueError, IndexError):
            continue
    return total


class Sampler:
    def __init__(self, proc: Path = Path("/proc"), sysblock: Path = Path("/sys/block")):
        self.proc, self.sysblock = proc, sysblock
        self._last: tuple[float, int] | None = None

    def sample(self, now: float) -> Signals:
        mem = parse_meminfo((self.proc / "meminfo").read_text())
        psi = parse_psi((self.proc / "pressure/memory").read_text())
        pswp = parse_pswpout((self.proc / "vmstat").read_text())
        rate = 0.0
        if self._last and now > self._last[0]:
            rate = (pswp - self._last[1]) / (now - self._last[0])
        self._last = (now, pswp)
        # MemAvailable counts pages that compression would only shrink, not free: with a compressed swap in use the
        # headroom is overstated by what the pools already hold, so take that off (conservative, #463)
        pools = mem.get("Zswap", 0) + zram_bytes(self.sysblock)
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
