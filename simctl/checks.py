"""Host and profile checks. Pure functions over injected readers, so they unit-test without a host."""
import json
import os
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from governor.watchdog import container_violations

GB = 1024**3
SAFETY_FLOOR = int(1.5 * GB)  # MemAvailable must stay above this after the profile starts
GOV_SOCKET = "/run/hpcsim/gov.sock"
OK, WARN, FAIL = "ok", "warn", "fail"


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


def kernel_tuple(release: str) -> tuple[int, int]:
    m = re.match(r"(\d+)\.(\d+)", release)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def check_cgroup_v2(root: Path = Path("/sys/fs/cgroup")) -> Check:
    if (root / "cgroup.controllers").exists():
        return Check("cgroup v2", OK, "unified hierarchy")
    return Check("cgroup v2", FAIL, "cgroup v1 or unmounted",
                 "boot with systemd.unified_cgroup_hierarchy=1")


def check_psi(path: Path = Path("/proc/pressure/memory")) -> Check:
    if path.exists():
        return Check("memory PSI", OK, "available")
    return Check("memory PSI", FAIL, "/proc/pressure/memory missing", "boot with psi=1")


def check_kernel(release: str | None = None) -> Check:
    rel = release or os.uname().release
    if kernel_tuple(rel) >= (5, 19):
        return Check("kernel", OK, f"{rel} (memory.reclaim supported)")
    return Check("kernel", WARN, f"{rel} < 5.19", "memory.reclaim unavailable; L3 reclaim degrades")


def check_docker(runner=run) -> Check:
    r = runner(["docker", "info", "--format", "{{.CgroupDriver}} {{.CgroupVersion}}"])
    if r.returncode != 0:
        return Check("docker", FAIL, (r.stderr or "docker not reachable").strip()[:120],
                     "install Docker and add your user to the docker group")
    drv, _, ver = r.stdout.strip().partition(" ")
    if drv != "systemd" or ver != "2":
        return Check("docker cgroups", FAIL, f"driver={drv} version={ver}",
                     'set {"exec-opts":["native.cgroupdriver=systemd"]} in /etc/docker/daemon.json')
    return Check("docker", OK, "systemd cgroup driver, cgroup v2")


def check_libvirt(runner=run) -> Check:
    r = runner(["virsh", "-c", "qemu:///system", "list", "--all"])
    if r.returncode == 0:
        return Check("libvirt", OK, "qemu:///system reachable")
    return Check("libvirt", WARN, (r.stderr or "virsh missing").strip()[:120],
                 "only needed for the fidelity VM: add your user to the libvirt group")


def check_swap(meminfo: str | None = None, zram: bool | None = None) -> Check:
    text = meminfo if meminfo is not None else Path("/proc/meminfo").read_text()
    kb = {k: int(v.split()[0]) for k, _, v in (ln.partition(":") for ln in text.splitlines() if ":" in ln)}
    total, free = kb.get("SwapTotal", 0), kb.get("SwapFree", 0)
    z = zram if zram is not None else Path("/sys/block/zram0").exists()
    if total == 0:
        return Check("swap", WARN, "no swap", "enable zram: reclaim has nowhere to push pages")
    used_pct = 100 * (total - free) / total
    if used_pct > 90:
        return Check("swap", WARN, f"{used_pct:.0f}% used ({'zram' if z else 'disk'})",
                     "swap is nearly full: the host has little headroom; close other workloads")
    return Check("swap", OK, f"{used_pct:.0f}% used" + (" (zram)" if z else ""))


def check_memlock() -> Check:
    import resource

    _soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    need = 256 * 1024**2
    if hard == resource.RLIM_INFINITY or hard >= need:
        return Check("memlock", OK, "hard limit allows the governor's mlockall")
    return Check("memlock", FAIL, f"hard limit {hard} bytes", "set LimitMEMLOCK=infinity (the unit does)")


def check_tools(which=shutil.which) -> list[Check]:
    out = []
    for tool, need in (("docker", True), ("virsh", False), ("vagrant", False), ("kubectl", False),
                       ("k3d", False), ("tc", False)):
        if which(tool):
            out.append(Check(f"tool {tool}", OK, "found"))
        else:
            out.append(Check(f"tool {tool}", FAIL if need else WARN, "missing",
                             f"install {tool}" + ("" if need else " (needed by some profiles only)")))
    return out


def check_governor(path: str = GOV_SOCKET) -> Check:
    try:
        st = governor_request({"cmd": "status"}, path)
    except OSError as e:
        return Check("governor", WARN, f"not reachable at {path}: {e}",
                     "start it: python -m governor --socket " + path)
    return Check("governor", OK, f"level L{st['level']}{' (dry-run)' if st.get('dry_run') else ''}")


def governor_request(req: dict, path: str = GOV_SOCKET, timeout: float = 3) -> dict:
    with socket.socket(socket.AF_UNIX) as c:
        c.settimeout(timeout)
        c.connect(path)
        c.sendall(json.dumps(req).encode() + b"\n")
        return json.loads(c.makefile().readline())


def check_containers(runner=run) -> Check:
    ids = runner(["docker", "ps", "-q", "--filter", "label=hpcsim.class"])
    if ids.returncode != 0 or not ids.stdout.split():
        return Check("sim containers", OK, "none running")
    r = runner(["docker", "inspect", *ids.stdout.split()], 30)
    bad = container_violations(json.loads(r.stdout)) if r.returncode == 0 else ["docker inspect failed"]
    if bad:
        return Check("sim containers", FAIL, "; ".join(bad[:5]) + (f" (+{len(bad) - 5} more)" if len(bad) > 5 else ""),
                     "remove the extra privilege from the generated compose, or record an ADR exception")
    return Check("sim containers", OK, f"{len(ids.stdout.split())} running, unprivileged, memory-limited")


def run_all() -> list[Check]:
    return [check_cgroup_v2(), check_psi(), check_kernel(), check_docker(), check_libvirt(),
            check_swap(), check_memlock(), *check_tools(), check_containers(), check_governor()]


# ---- profile pre-flight (SPEC 6) ----

def load_profiles(root: Path = Path("profiles")) -> dict[str, float]:
    out = {}
    for f in sorted(root.glob("*.yaml")):
        d = yaml.safe_load(f.read_text())
        out[d["name"]] = float(d["budget_gb"])
    return out


def mem_available(meminfo: str | None = None) -> int:
    text = meminfo if meminfo is not None else Path("/proc/meminfo").read_text()
    for ln in text.splitlines():
        if ln.startswith("MemAvailable:"):
            return int(ln.split()[1]) * 1024
    return 0


def preflight(profile: str, profiles: dict[str, float], available: int,
              floor: int = SAFETY_FLOOR) -> tuple[bool, str]:
    """Allow the profile only if MemAvailable - budget stays above the floor."""
    if profile not in profiles:
        return False, f"unknown profile {profile!r}; known: {', '.join(sorted(profiles))}"
    budget = int(profiles[profile] * GB)
    left = available - budget
    if left >= floor:
        return True, f"{profile}: budget {budget / GB:.1f} GB, {left / GB:.1f} GB would remain"
    fits = [n for n, b in sorted(profiles.items(), key=lambda kv: -kv[1])
            if available - int(b * GB) >= floor]
    hint = f"try --profile {fits[0]}" if fits else "no profile fits; free memory first"
    return False, (f"{profile}: budget {budget / GB:.1f} GB leaves {left / GB:.1f} GB "
                   f"(floor {floor / GB:.1f} GB); {hint}")
