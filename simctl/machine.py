"""Initial machine check: can this box build and run workbench at all?

Standard library only, so it runs before the venv or any image exists:
    ./scripts/check-machine.sh        (or: python3 -m simctl.machine)
Pure functions over injected readers, so they unit-test without a host.
`benchctl doctor` is the later, deeper check of a running setup.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

GB = 1024**3
OK, WARN, FAIL = "ok", "warn", "fail"
MIN_RAM_GB, REC_RAM_GB = 8, 12  # total RAM: below the first the stack cannot fit, below the second it is tight
MIN_DISK_GB, REC_DISK_GB = 10, 20  # free space for images and Prometheus data
MIN_CPUS, REC_CPUS = 2, 4
MIN_PYTHON = (3, 11)
PORTS = {8088: "web UI", 8080: "jira-mock", 8081: "bcm-mock", 8000: "bmc-farm", 9400: "gpu-exporter",
         3000: "Grafana", 9090: "Prometheus"}
SAFETY_FLOOR = 1.0  # GB of MemAvailable that must remain after a profile starts (same as benchctl preflight)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def run(cmd: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def meminfo_gb(text: str | None = None) -> tuple[float, float]:
    """(MemTotal, MemAvailable) in GiB."""
    text = text if text is not None else Path("/proc/meminfo").read_text()
    got = {}
    for ln in text.splitlines():
        key, _, rest = ln.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            got[key] = int(rest.split()[0]) / 1024 / 1024
    return got.get("MemTotal", 0.0), got.get("MemAvailable", 0.0)


def check_os(system: str | None = None, release_text: str | None = None) -> Check:
    system = system or platform.system()
    if system == "Darwin":
        return Check("os", FAIL, "macOS cannot host the cluster",
                     "run workbench on Linux; macOS is only an external agent (README, macOS testing)")
    if system != "Linux":
        return Check("os", FAIL, f"{system} is not supported", "use Ubuntu 22.04 or newer")
    try:
        text = release_text if release_text is not None else Path("/etc/os-release").read_text()
        name = re.search(r'^PRETTY_NAME="?(.*?)"?$', text, re.MULTILINE)
        label = name.group(1) if name else "Linux"
    except OSError:
        label = "Linux"
    if "ubuntu" not in label.lower():
        return Check("os", WARN, f"{label} (tested on Ubuntu)", "expect to adapt the install steps")
    return Check("os", OK, label)


def check_cpus(count: int | None = None) -> Check:
    n = count if count is not None else (os.cpu_count() or 1)
    if n < MIN_CPUS:
        return Check("cpu", FAIL, f"{n} core(s)", f"at least {MIN_CPUS} cores are needed")
    if n < REC_CPUS:
        return Check("cpu", WARN, f"{n} cores", f"{REC_CPUS}+ cores recommended; the std profile will be slow")
    return Check("cpu", OK, f"{n} cores")


def check_ram(total_gb: float | None = None) -> Check:
    total = total_gb if total_gb is not None else meminfo_gb()[0]
    if total < MIN_RAM_GB:
        return Check("ram", FAIL, f"{total:.1f} GB total", f"at least {MIN_RAM_GB} GB are needed")
    if total < REC_RAM_GB:
        return Check("ram", WARN, f"{total:.1f} GB total", "only the min profile is realistic below 12 GB")
    return Check("ram", OK, f"{total:.1f} GB total")


def check_disk(path: str = ".", free_gb: float | None = None) -> Check:
    free = free_gb if free_gb is not None else shutil.disk_usage(path).free / GB
    if free < MIN_DISK_GB:
        return Check("disk", FAIL, f"{free:.0f} GB free", f"at least {MIN_DISK_GB} GB are needed for images and data")
    if free < REC_DISK_GB:
        return Check("disk", WARN, f"{free:.0f} GB free", f"{REC_DISK_GB}+ GB recommended")
    return Check("disk", OK, f"{free:.0f} GB free")


def check_python(version: tuple[int, int] | None = None) -> Check:
    v = version or sys.version_info[:2]
    label = f"python {v[0]}.{v[1]}"
    if v < MIN_PYTHON:
        return Check("python", FAIL, label, f"python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is needed (Ubuntu 24.04 has it)")
    return Check("python", OK, label)


def check_cgroup_v2(root: Path = Path("/sys/fs/cgroup")) -> Check:
    if (root / "cgroup.controllers").exists():
        return Check("cgroup v2", OK, "unified hierarchy")
    return Check("cgroup v2", FAIL, "legacy cgroup v1", "boot with systemd.unified_cgroup_hierarchy=1")


def check_docker(runner=run, which=shutil.which) -> list[Check]:
    if not which("docker"):
        return [Check("docker", FAIL, "not installed", "install Docker Engine (docs.docker.com/engine/install/ubuntu)")]
    out = [Check("docker", OK, "installed")]
    info = runner(["docker", "info", "--format", "{{.ServerVersion}}"])
    if info.returncode == 0:
        out.append(Check("docker daemon", OK, f"running, server {info.stdout.strip()}"))
    else:
        out.append(Check("docker daemon", FAIL, (info.stderr or "not reachable").strip().splitlines()[0][:120],
                         "start it (sudo systemctl start docker) and add yourself to the docker group"))
    comp = runner(["docker", "compose", "version", "--short"])
    if comp.returncode == 0:
        out.append(Check("docker compose", OK, f"v{comp.stdout.strip().lstrip('v')}"))
    else:
        out.append(Check("docker compose", FAIL, "plugin missing", "sudo apt install docker-compose-plugin"))
    return out


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def check_ports(free=port_free) -> Check:
    busy = [f"{p} ({what})" for p, what in PORTS.items() if not free(p)]
    if busy:
        return Check("ports", WARN, "in use: " + ", ".join(busy),
                     "stop what uses them, or this is a running stack (benchctl down)")
    return Check("ports", OK, "all free on 127.0.0.1")


def check_optional(which=shutil.which, kvm: bool | None = None) -> list[Check]:
    has_kvm = kvm if kvm is not None else Path("/dev/kvm").exists()
    out = [Check("kvm", OK, "/dev/kvm present") if has_kvm else
           Check("kvm", WARN, "no /dev/kvm", "only the fidelity VM needs it")]
    for tool, why in (("k3d", "Kubernetes profiles"), ("kubectl", "Kubernetes profiles"), ("virsh", "the fidelity VM")):
        out.append(Check(tool, OK, "found") if which(tool) else Check(tool, WARN, "missing", f"needed for {why} only"))
    return out


def profile_budgets(root: Path = Path("profiles")) -> dict[str, float]:
    out = {}
    for f in sorted(root.glob("*.yaml")):
        text = f.read_text()
        name = re.search(r"^name:\s*(\S+)", text, re.MULTILINE)
        budget = re.search(r"^budget_gb:\s*([\d.]+)", text, re.MULTILINE)
        if name and budget:
            out[name.group(1)] = float(budget.group(1))
    return out


def check_profiles(avail_gb: float, budgets: dict[str, float]) -> Check:
    fits = [n for n, b in sorted(budgets.items(), key=lambda kv: kv[1]) if avail_gb - b >= SAFETY_FLOOR]
    if not budgets:
        return Check("profiles", WARN, "no profiles found", "run from the repo root")
    if not fits:
        return Check("profiles", FAIL, f"{avail_gb:.1f} GB available, none fits",
                     "close other applications, then re-run")
    return Check("profiles", OK, f"{avail_gb:.1f} GB available now; fits: {', '.join(fits)} "
                 f"(largest: {fits[-1]}, budget {budgets[fits[-1]]:g} GB)")


def run_all() -> list[Check]:
    total, avail = meminfo_gb() if platform.system() == "Linux" else (0.0, 0.0)
    linux = platform.system() == "Linux"
    out = [check_os(), check_cpus(), check_ram(total) if linux else Check("ram", WARN, "unknown", "")]
    out += [check_disk(), check_python()]
    if linux:
        out += [check_cgroup_v2()]
    out += [*check_docker(), check_ports(), *check_optional()]
    if linux:
        out.append(check_profiles(avail, profile_budgets()))
    return out


def report(results: list[Check]) -> str:
    icon = {OK: "ok  ", WARN: "WARN", FAIL: "FAIL"}
    lines = []
    for c in results:
        lines.append(f"[{icon[c.status]}] {c.name}: {c.detail}")
        if c.fix and c.status != OK:
            lines.append(f"       fix: {c.fix}")
    fails = sum(c.status == FAIL for c in results)
    warns = sum(c.status == WARN for c in results)
    if fails:
        lines.append(f"\nNOT READY: {fails} requirement(s) missing. Fix the FAIL lines, then run this again.")
    else:
        lines.append(f"\nREADY: this machine can build and run workbench ({warns} warning(s)). "
                     "Next: python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'")
    return "\n".join(lines)


def main() -> int:
    results = run_all()
    print(report(results))
    return 1 if any(c.status == FAIL for c in results) else 0


if __name__ == "__main__":
    sys.exit(main())
