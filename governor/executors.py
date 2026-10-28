"""Real actions: cgroup v2 files for freeze/throttle/reclaim, Docker CLI for stop/start, virsh for VMs.

cgroup writes go through pre-opened file handles (MitigationPath) so L3 needs no new allocation
(GV-8). Docker/virsh calls spawn a process; they are used only at L4+ and are time-boxed.
"""
import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from .memlock import MitigationPath
from .planner import Action, Group

log = logging.getLogger("governor.exec")

CGROUP_ROOT = Path("/sys/fs/cgroup")
THROTTLE_FRACTION = 0.8  # memory.high = 80% of current usage
RECLAIM_FRACTION = 0.5  # ask the kernel to reclaim half of current usage
ARRIVALS_FLAG = "/run/hpcsim/arrivals.paused"
LIBVIRT_URI = "qemu:///system"  # a user-session virsh would not see system domains


def run(cmd: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def cgroup_dir(c: dict) -> Path:
    """Cgroup directory of one `docker inspect` entry (systemd driver layout)."""
    parent = c["HostConfig"].get("CgroupParent") or "system.slice"
    return CGROUP_ROOT / parent / f"docker-{c['Id']}.scope"


def discover_docker(run_cmd: Callable = run) -> tuple[list[Group], dict[str, Path]]:
    """Containers labelled hpcsim.class=P<n> -> groups and their cgroup directories."""
    ps = run_cmd(["docker", "ps", "-q", "--filter", "label=hpcsim.class"])
    ids = ps.stdout.split()
    if not ids:
        return [], {}
    info = json.loads(run_cmd(["docker", "inspect", *ids]).stdout)
    groups, cgroups = [], {}
    for c in info:
        name = c["Name"].lstrip("/")
        prio = int(c["Config"]["Labels"]["hpcsim.class"].lstrip("Pp"))
        groups.append(Group(name, prio))
        cgroups[name] = cgroup_dir(c)
    return groups, cgroups


class Executor:
    def __init__(self, groups: list[Group], cgroups: dict[str, Path],
                 path: MitigationPath | None = None, run_cmd: Callable = run,
                 arrivals_flag: str = ARRIVALS_FLAG):
        self.kinds = {g.name: g.kind for g in groups}
        self.cgroups, self.path, self.run = cgroups, path or MitigationPath(), run_cmd
        self.arrivals_flag = arrivals_flag
        self.prealloc()

    def refresh(self, groups: list[Group], cgroups: dict[str, Path]) -> None:
        """Swap in a new inventory: close handles of vanished cgroups, pre-open the new ones."""
        for name, cg in self.cgroups.items():
            if cgroups.get(name) != cg:
                self.path.forget(str(cg))
        self.kinds = {g.name: g.kind for g in groups}
        self.cgroups = cgroups
        self.prealloc()

    def prealloc(self) -> None:
        """Open every writable control file now, before pressure exists."""
        for cg in self.cgroups.values():
            for f in ("cgroup.freeze", "memory.high", "memory.reclaim", "cgroup.kill"):
                p = cg / f
                if p.exists():
                    try:
                        self.path.open(str(p))
                    except OSError as e:
                        log.warning("cannot open %s: %s", p, e)

    def _write(self, name: str, fname: str, data: str) -> None:
        p = str(self.cgroups[name] / fname)
        self.path.write(p, data.encode())

    def _current(self, name: str) -> int:
        return int((self.cgroups[name] / "memory.current").read_text())

    def __call__(self, a: Action) -> None:
        verb, target = a.verb.split("=")[0], a.target
        if verb == "notify":
            return
        if verb == "stop-arrivals":
            Path(self.arrivals_flag).parent.mkdir(parents=True, exist_ok=True)
            Path(self.arrivals_flag).touch()
        elif verb == "resume-arrivals":
            Path(self.arrivals_flag).unlink(missing_ok=True)
        elif verb == "freeze":
            self._write(target, "cgroup.freeze", "1")
        elif verb == "thaw":
            self._write(target, "cgroup.freeze", "0")
        elif verb == "throttle":
            self._write(target, "memory.high", str(int(self._current(target) * THROTTLE_FRACTION)))
        elif verb == "unthrottle":
            self._write(target, "memory.high", "max")
        elif verb == "reclaim":
            try:
                self._write(target, "memory.reclaim", str(int(self._current(target) * RECLAIM_FRACTION)))
            except BlockingIOError:  # EAGAIN: kernel reclaimed less than asked (no swap/zram); not a failure
                log.warning("reclaim on %s only partially satisfied", target)
        elif verb == "stop":
            # cgroup.kill through a pre-opened handle stops a runaway instantly and needs no
            # allocation; docker stop then reconciles container state.
            cg = self.cgroups.get(target)
            if cg and self.path.has(str(cg / "cgroup.kill")):
                self._write(target, "cgroup.kill", "1")
            self._check(self.run(["docker", "stop", "-t", "2", target], timeout=20), a)
        elif verb == "start":
            self._check(self.run(["docker", "start", target]), a)
        elif verb == "save":
            self._check(self.run(["virsh", "-c", LIBVIRT_URI, "managedsave", target], timeout=60), a)
        else:
            raise NotImplementedError(f"verb {a.verb}")

    @staticmethod
    def _check(r: subprocess.CompletedProcess, a: Action) -> None:
        if r.returncode != 0:
            raise RuntimeError(f"{a} failed: {r.stderr.strip()}")
