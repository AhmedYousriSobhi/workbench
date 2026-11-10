"""Phase 0 measurement: read-only memory sampling of host, containers, processes and VMs."""
import json
import math
import os
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


OVERHEAD = ("containerd-shim", "dockerd", "containerd")  # Docker's own processes: outside every container's cgroup, so `docker stats` never counts them


def docker_overhead(proc: Path = Path("/proc")) -> dict[str, int]:
    """Bytes of the Docker plumbing the containers cost (issue 456): `shim` (all containerd-shims summed), `dockerd`,
    `containerd`, and `shims` (how many shims, a count). One shim runs per container, so `shim / shims` is the per-container
    cost that grows with the node count. Root-owned processes hide `smaps_rollup` from a normal user: pss_kb then falls
    back to VmRSS, which counts shared pages in every shim (the figure is then an upper bound)."""
    out = {"shim": 0, "dockerd": 0, "containerd": 0, "shims": 0}
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            comm = (d / "comm").read_text().strip()
        except OSError:
            continue
        if comm not in OVERHEAD:
            continue
        kb = pss_kb(d)
        if kb:
            key = "shim" if comm == "containerd-shim" else comm
            out[key] += kb * 1024
            out["shims"] += comm == "containerd-shim"
    return out


def overhead_summary(stats: dict) -> dict[str, int]:
    """From an aggregated run: MB per container (the shims' peak over their count) and MB fixed (dockerd + containerd). {} when
    the run has no shims (Docker was not running, or the run predates this measurement)."""
    shims = stats.get("overhead:shims", {}).get("peak", 0)
    if not shims:
        return {}
    shim = stats.get("overhead:shim", {}).get("peak", 0)
    fixed = stats.get("overhead:dockerd", {}).get("peak", 0) + stats.get("overhead:containerd", {}).get("peak", 0)
    return {"shims": shims, "per_container_mb": math.ceil(shim / shims / 1024**2), "fixed_mb": math.ceil(fixed / 1024**2)}


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
    snap.update({f"overhead:{k}": v for k, v in docker_overhead().items()})
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
        if key.startswith(("host:", "overhead:")):  # overhead has its own lines below (shims is a count, not bytes)
            continue
        i = idle["stats"].get(key, {}).get("mean", 0)
        p = loaded["stats"].get(key, {}).get("peak", 0)
        rows.append(f"| {key} | {i // 1024**2} | {p // 1024**2} | {suggest_budget_mb(max(i, p))} |")
    if ov := overhead_summary(loaded["stats"]):
        rows += ["", (f"Docker overhead (outside every container, not in `docker stats`): {ov['shims']} containerd-shims, "
                      f"{ov['per_container_mb']} MB each (peak, grows with every container); dockerd + containerd {ov['fixed_mb']} MB fixed. "
                      "Keep it for the plan with `benchctl measure overhead --save`.")]
    return "\n".join(rows)


APP_HEADROOM = 1.1
APP_ROUND_MB = 100
APP_MAX_NAMES = 4  # more distinct process names than this usually means a loose --match


def _ancestors() -> set[int]:
    """This process and its parents (the `benchctl` wrapper whose command line holds the --match text)."""
    out, pid = set(), os.getpid()
    while pid > 1 and pid not in out:
        out.add(pid)
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return out


def app_pids(matches: list[str], pids: list[int], proc: Path = Path("/proc"), skip: set[int] | None = None) -> list[int]:
    """Pids whose name or command line contains a --match text (case-insensitive), plus the given --pid values."""
    skip = _ancestors() if skip is None else skip
    found = {p for p in pids if (proc / str(p)).is_dir()}
    needles = [m.lower() for m in matches if m]
    for d in proc.iterdir():
        if not d.name.isdigit() or int(d.name) in skip or not needles:
            continue
        try:
            text = ((d / "comm").read_text() + " " + (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")).lower()
        except OSError:
            continue
        if any(n in text for n in needles):
            found.add(int(d.name))
    return sorted(found - skip)


def app_bytes(matches: list[str], pids: list[int], proc: Path = Path("/proc")) -> tuple[int, int]:
    """(PSS bytes summed over the app's processes, how many processes)."""
    total, count = 0, 0
    for pid in app_pids(matches, pids, proc):
        kb = pss_kb(proc / str(pid))
        if kb:
            total, count = total + kb * 1024, count + 1
    return total, count


def app_breakdown(matches: list[str], pids: list[int], proc: Path = Path("/proc")) -> dict[str, tuple[int, int]]:
    """Per process name: (PSS bytes, processes), largest first; shows what a --match really caught."""
    out: dict[str, list[int]] = {}
    for pid in app_pids(matches, pids, proc):
        kb = pss_kb(proc / str(pid))
        if not kb:
            continue
        try:
            name = (proc / str(pid) / "comm").read_text().strip()
        except OSError:
            continue
        cur = out.setdefault(name, [0, 0])
        cur[0], cur[1] = cur[0] + kb * 1024, cur[1] + 1
    return {k: (v[0], v[1]) for k, v in sorted(out.items(), key=lambda kv: -kv[1][0])}


def sample_app(matches: list[str], pids: list[int], duration: float, interval: float, take=None,
               sleep=time.sleep, proc: Path = Path("/proc")) -> list[tuple[int, int]]:
    """One (bytes, processes) sample now and one every interval; duration 0 runs until Ctrl-C."""
    take = take or app_bytes
    out = [take(matches, pids, proc)]
    try:
        for _ in range(int(duration // interval)) if duration else iter(int, 1):
            sleep(interval)
            out.append(take(matches, pids, proc))
    except KeyboardInterrupt:
        pass
    return out


def suggest_app_mb(peak_bytes: int) -> int:
    """The peak plus about 10 percent, rounded up to 100 MB."""
    return math.ceil(peak_bytes * APP_HEADROOM / 1024**2 / APP_ROUND_MB) * APP_ROUND_MB


def app_summary(samples: list[tuple[int, int]]) -> dict[str, int]:
    """Peak and typical (median) bytes over the samples that saw the app, the most processes at once, the suggested reserve."""
    seen = [(b, n) for b, n in samples if n]
    if not seen:
        return {}
    sizes = sorted(b for b, _ in seen)
    peak = sizes[-1]
    return {"samples": len(samples), "peak": peak, "typical": sizes[len(sizes) // 2],
            "processes": max(n for _, n in seen), "suggested_mb": suggest_app_mb(peak)}
