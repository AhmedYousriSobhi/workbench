"""Diagnostic bundle: everything needed to debug a problem in one tarball, secrets masked."""
import io
import re
import tarfile
import time
from pathlib import Path

from . import checks

SECRET = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
                    r"(?i:token|password|secret)(\"?\s*[:=]\s*\"?)[^\s\"',]+|https://[^/@\s]+@)")


def mask(text: str) -> str:
    def sub(m: re.Match) -> str:
        s = m.group(0)
        if s.startswith("https://"):
            return "https://***@"
        if m.group(2):
            return s[: s.index(m.group(2)) + len(m.group(2))] + "***"
        return "***"

    return SECRET.sub(sub, text)


def collect() -> dict[str, str]:
    r = checks.run
    files: dict[str, str] = {}
    files["doctor.txt"] = "\n".join(f"{c.status:5} {c.name}: {c.detail}  {c.fix}" for c in checks.run_all())
    for name in ("meminfo", "vmstat", "pressure/memory", "pressure/cpu", "pressure/io", "loadavg"):
        p = Path("/proc") / name
        files[f"proc_{name.replace('/', '_')}.txt"] = p.read_text() if p.exists() else "missing"
    files["free.txt"] = r(["free", "-m"]).stdout
    files["swapon.txt"] = r(["swapon", "--show"]).stdout
    files["docker_ps.txt"] = r(["docker", "ps", "-a", "--format",
                                "{{.Names}}\t{{.Status}}\t{{.Label \"hpcsim.class\"}}"]).stdout
    files["docker_stats.txt"] = r(["docker", "stats", "--no-stream", "--format",
                                   "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"], timeout=20).stdout
    files["virsh_list.txt"] = r(["virsh", "-c", "qemu:///system", "list", "--all"]).stdout
    files["top_mem.txt"] = r(["ps", "-eo", "pid,rss,comm", "--sort=-rss"]).stdout[:4000]
    try:
        files["governor_status.json"] = str(checks.governor_request({"cmd": "status"}))
    except OSError as e:
        files["governor_status.json"] = f"unreachable: {e}"
    files["governor_journal.txt"] = r(["journalctl", "-u", "hpcsim-governor", "-n", "200",
                                       "--no-pager"]).stdout
    files["git_rev.txt"] = r(["git", "rev-parse", "HEAD"]).stdout + r(["git", "status", "-sb"]).stdout
    sim = Path("sim.yaml")
    if sim.exists():
        files["sim.yaml"] = sim.read_text()
    return {k: mask(v) for k, v in files.items()}


def write_bundle(out: Path | None = None) -> Path:
    out = out or Path(f"workbench-diag-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz")
    with tarfile.open(out, "w:gz") as tar:
        for name, text in collect().items():
            data = text.encode()
            info = tarfile.TarInfo(f"diag/{name}")
            info.size, info.mtime = len(data), int(time.time())
            tar.addfile(info, io.BytesIO(data))
    return out
