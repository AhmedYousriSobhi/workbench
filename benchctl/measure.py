"""Phase 0 measurement: read-only memory sampling of host, containers, processes and VMs."""
import json
import math
import re
import time
from collections import defaultdict
from pathlib import Path

from governor.executors import LIBVIRT_URI

from . import checks

UNITS = {"b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "kb": 1000, "mb": 1000**2, "gb": 1000**3}
HEADROOM = 1.2
TOP_PROCS = 15


def parse_size(text: str) -> int:
    """'12.3MiB' -> bytes."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([A-Za-z]+)\s*", text)
    if not m or m.group(2).lower() not in UNITS:
        raise ValueError(f"bad size: {text!r}")
    return int(float(m.group(1)) * UNITS[m.group(2).lower()])


def parse_docker_stats(out: str) -> dict[str, int]:
    """Lines of 'name<TAB>12MiB / 1GiB' -> {name: bytes}."""
    res: dict[str, int] = {}
    for line in out.splitlines():
        name, _, usage = line.partition("\t")
        if name and usage:
            res[name] = parse_size(usage.split("/")[0])
    return res


def parse_dommemstat(out: str) -> int:
    """virsh dommemstat output -> resident bytes (rss, else actual balloon size)."""
    stats = {k: int(v) for k, v in (ln.split() for ln in out.splitlines() if len(ln.split()) == 2)
             if v.isdigit()}
    return stats.get("rss", stats.get("actual", 0)) * 1024


def pss_kb(pid_dir: Path) -> int | None:
    """Proportional set size in kB; falls back to VmRSS when smaps_rollup is not readable."""
    for fname, key in (("smaps_rollup", "Pss:"), ("status", "VmRSS:")):
        try:
            for line in (pid_dir / fname).read_text().splitlines():
                if line.startswith(key):
                    return int(line.split()[1])
        except OSError:
            continue
    return None


def processes(proc: Path = Path("/proc")) -> dict[str, int]:
    """Bytes per process name (summed over pids), top N only."""
    sums: dict[str, int] = defaultdict(int)
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            comm = (d / "comm").read_text().strip()
        except OSError:
            continue
        kb = pss_kb(d)
        if kb:
            sums[comm] += kb * 1024
    return dict(sorted(sums.items(), key=lambda kv: -kv[1])[:TOP_PROCS])


def host() -> dict[str, int]:
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        info[k] = int(v.split()[0]) * 1024 if v.split() else 0
    return {"used": info["MemTotal"] - info["MemAvailable"], "available": info["MemAvailable"],
            "swap_used": info["SwapTotal"] - info["SwapFree"]}


def containers(runner=checks.run) -> dict[str, int]:
    r = runner(["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"], 30)
    return parse_docker_stats(r.stdout) if r.returncode == 0 else {}


def vms(runner=checks.run) -> dict[str, int]:
    r = runner(["virsh", "-c", LIBVIRT_URI, "list", "--name"])
    out = {}
    for name in r.stdout.split() if r.returncode == 0 else []:
        s = runner(["virsh", "-c", LIBVIRT_URI, "dommemstat", name])
        if s.returncode == 0:
            out[name] = parse_dommemstat(s.stdout)
    return out


def snapshot() -> dict[str, int]:
    """One flat {kind:name: bytes} reading. Read-only: nothing is touched."""
    snap = {f"host:{k}": v for k, v in host().items()}
    snap.update({f"docker:{k}": v for k, v in containers().items()})
    snap.update({f"vm:{k}": v for k, v in vms().items()})
    snap.update({f"proc:{k}": v for k, v in processes().items()})
    return snap


def aggregate(snaps: list[dict[str, int]]) -> dict[str, dict[str, int]]:
    """Per key: mean and peak bytes over the window (a missing sample counts as 0)."""
    keys = {k for s in snaps for k in s}
    return {k: {"mean": sum(s.get(k, 0) for s in snaps) // len(snaps),
                "peak": max(s.get(k, 0) for s in snaps)} for k in sorted(keys)}


def sample(duration: float, interval: float, take=snapshot, sleep=time.sleep) -> list[dict[str, int]]:
    snaps = [take()]
    for _ in range(int(duration // interval)):
        sleep(interval)
        snaps.append(take())
    return snaps


def save(label: str, snaps: list[dict[str, int]], out_dir: Path = Path("docs/measurements")) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{label}.json"
    path.write_text(json.dumps({"label": label, "taken": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "samples": len(snaps), "stats": aggregate(snaps)}, indent=2) + "\n")
    return path


def suggest_budget_mb(peak_bytes: int) -> int:
    return math.ceil(peak_bytes * HEADROOM / 1024**2)


def report(idle: dict, loaded: dict | None = None) -> str:
    """Markdown table of idle mean vs loaded peak with a suggested budget (peak x headroom)."""
    loaded = loaded or idle
    rows = ["| Component | Idle MB | Loaded peak MB | Suggested budget MB |", "|---|---|---|---|"]
    for key in sorted(set(idle["stats"]) | set(loaded["stats"])):
        if key.startswith("host:"):
            continue
        i = idle["stats"].get(key, {}).get("mean", 0)
        p = loaded["stats"].get(key, {}).get("peak", 0)
        rows.append(f"| {key} | {i // 1024**2} | {p // 1024**2} | {suggest_budget_mb(max(i, p))} |")
    return "\n".join(rows)
