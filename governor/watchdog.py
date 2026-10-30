"""Security and limit-drift monitoring for sim containers (report only; ADR 0002).

Pure stdlib: the governor is a root service and must not import benchctl's dependencies.
"""
import json
import logging
from collections.abc import Callable
from pathlib import Path

from .executors import cgroup_dir, run

log = logging.getLogger("governor.watchdog")


# slurmrestd options we accept (ADR 0003/0004). Anything else (disable_user_check, become_user, typos)
# weakens it further and is reported.
ACCEPTED_SLURMRESTD = frozenset({"disable_unshare_sysv", "disable_unshare_files"})


def _slurmrestd_options(c: dict) -> set[str]:
    env = dict(e.partition("=")[::2] for e in (c.get("Config") or {}).get("Env") or [])
    return {o for o in env.get("SLURMRESTD_SECURITY", "").split(",") if o}


def accepted_exceptions(inspected: list[dict]) -> list[str]:
    """Known, documented deviations (not violations); doctor lists them so they stay visible."""
    return [f"{c['Name'].lstrip('/')}: slurmrestd skips its unshare steps (ADR 0004)"
            for c in inspected if _slurmrestd_options(c) & ACCEPTED_SLURMRESTD]


def container_violations(inspected: list[dict]) -> list[str]:
    """Security/limit gaps in `docker inspect` output for sim containers."""
    bad = []
    for c in inspected:
        name, hc = c["Name"].lstrip("/"), c["HostConfig"]
        if hc.get("Privileged"):
            bad.append(f"{name}: privileged")
        if hc.get("CapAdd"):
            bad.append(f"{name}: extra capabilities {hc['CapAdd']}")
        if any("unconfined" in o for o in hc.get("SecurityOpt") or []):
            bad.append(f"{name}: confinement disabled {hc['SecurityOpt']}")
        if hc.get("NetworkMode") == "host" or hc.get("PidMode") == "host":
            bad.append(f"{name}: shares host network/pid namespace")
        if any(m.get("Source", "").endswith("docker.sock") for m in c.get("Mounts") or []):
            bad.append(f"{name}: mounts the Docker socket")
        if not hc.get("Memory"):
            bad.append(f"{name}: no memory limit")
        extra = _slurmrestd_options(c) - ACCEPTED_SLURMRESTD
        if extra:
            bad.append(f"{name}: SLURMRESTD_SECURITY has unaccepted options {sorted(extra)}")
    return bad


def limit_drift(inspected: list[dict], cg_dir: Callable[[dict], Path] = cgroup_dir) -> list[str]:
    """Containers whose live memory.max differs from the limit Docker was asked to set."""
    bad = []
    for c in inspected:
        declared = c["HostConfig"].get("Memory") or 0
        try:
            raw = (cg_dir(c) / "memory.max").read_text().strip()
        except OSError:
            continue  # cgroup not visible (e.g. non-root): nothing to compare
        actual = None if raw == "max" else int(raw)
        if declared and actual != declared:
            bad.append(f"{c['Name'].lstrip('/')}: memory.max {raw} differs from declared {declared}")
    return bad


class Watchdog:
    """Lists labelled containers and returns the current set of violations."""

    def __init__(self, run_cmd: Callable = run, cg_dir: Callable[[dict], Path] = cgroup_dir):
        self.run_cmd, self.cg_dir = run_cmd, cg_dir

    def scan(self) -> list[str]:
        ps = self.run_cmd(["docker", "ps", "-q", "--filter", "label=workbench.class"])
        ids = ps.stdout.split() if ps.returncode == 0 else []
        if not ids:
            return []
        r = self.run_cmd(["docker", "inspect", *ids])
        if r.returncode != 0:
            raise RuntimeError(f"docker inspect failed: {r.stderr.strip()[:100]}")
        info = json.loads(r.stdout)
        return sorted(container_violations(info) + limit_drift(info, self.cg_dir))
