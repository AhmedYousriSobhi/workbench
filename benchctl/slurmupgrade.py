"""Upgrade Slurm in a running cluster, one daemon at a time (issue 221, ADR 0079).

A scenario step `target: slurm-upgrade, do: {to: 26.05.5, daemons: [slurmdbd]}` copies the `to` release's /opt/slurm out of that
release's image into the named running containers and restarts only that daemon: containers are never recreated, so a job's
slurmstepd keeps running, as with a package upgrade on a real node. SchedMD's order is slurmdbd, then slurmctld (slurmrestd with it),
then the slurmd nodes, then the login nodes (client commands only, nothing to restart).

Names: `slurmdbd`, `slurmctld`, `slurmrestd`, `login`, or a node name (its slurmd is restarted by the node's supervisor).
"""
from __future__ import annotations

import inspect
import time
from pathlib import Path

from . import slurmver

SERVICES = {"slurmdbd": "workbench-slurmdbd-1", "slurmctld": "workbench-slurmctld-1", "slurmrestd": "workbench-slurmrestd-1",
            "login": "workbench-login-1"}
ORDER = ("slurmdbd", "slurmctld", "slurmrestd")
ROOT = Path(__file__).resolve().parent.parent
BUILD_S, COPY_S, SETTLE_S = 1800, 300, 90


def tag_of(to: str) -> str:
    return slurmver.tag({"slurm": {"version": to}})


def image(to: str) -> str:
    return f"workbench/node-base:{tag_of(to)}"


def container(name: str) -> str:
    return SERVICES.get(name) or f"workbench-{name}-1"


def problems(do: dict, nodes: set[str]) -> list[str]:
    out = []
    if do.get("to") not in slurmver.VERSIONS:
        out.append(f"upgrade.to is {do.get('to')}: use one of {', '.join(sorted(slurmver.VERSIONS))}")
    names = do.get("daemons")
    if not isinstance(names, list) or not names:
        return out + ["upgrade needs daemons: a list of slurmdbd, slurmctld, slurmrestd, login or node names"]
    return out + [f"upgrade: unknown daemon {n!r}" for n in names if n not in SERVICES and n not in nodes]


def _call(runner, cmd: list[str], timeout: float):
    try:
        takes = "timeout" in inspect.signature(runner).parameters
    except (TypeError, ValueError):
        takes = False
    return runner(cmd, timeout=timeout) if takes else runner(cmd)


def version_in(name: str, runner) -> str:
    """The release installed in the container ("26.05.5"), or "" when it cannot be read."""
    r = _call(runner, ["docker", "exec", container(name), "/opt/slurm/bin/sinfo", "-V"], 20)
    return r.stdout.split()[-1] if r.returncode == 0 and r.stdout.split() else ""


def ensure_image(to: str, runner, say=print) -> str | None:
    """None when the release's image exists (built now if it was missing), else why not."""
    img = image(to)
    if _call(runner, ["docker", "image", "inspect", img], 20).returncode == 0:
        return None
    say(f"building {img} (one-off, a few minutes)")
    cmd = ["docker", "build", "-q", "-t", img, *([] if to == slurmver.DEFAULT else
           ["--build-arg", f"SLURM_VERSION={to}", "--build-arg", f"SLURM_SHA256={slurmver.VERSIONS[to]}"]), str(ROOT / "images/node-base")]
    r = _call(runner, cmd, BUILD_S)
    return None if r.returncode == 0 else f"building {img} failed: {(r.stderr or r.stdout).strip()[-300:]}"


SWAP = ("set -e; rm -rf /opt/slurm.prev; mv /opt/slurm /opt/slurm.prev; mv /opt/slurm.next /opt/slurm; ldconfig; rm -rf /opt/slurm.prev")


def swap_commands(name: str, source: str) -> list[list[str]]:
    """Put the new tree beside the old one and rename it into place: running processes keep their old files open."""
    c = container(name)
    return [["docker", "exec", "-u", "0", c, "sh", "-c", "rm -rf /opt/slurm.next && mkdir /opt/slurm.next"],
            ["sh", "-c", f"docker cp {source}:/opt/slurm/. - | docker cp - {c}:/opt/slurm.next"],  # docker cp cannot copy between containers; a tar stream keeps owners and modes
            ["docker", "exec", "-u", "0", c, "sh", "-c", SWAP]]


def restart_command(name: str) -> list[str] | None:
    if name == "login":
        return None
    if name in SERVICES:  # the daemon is the container's main process: restart the container, its disk (and the new tree) stays
        return ["docker", "restart", "-t", "10", container(name)]
    return ["docker", "exec", container(name), "pkill", "-x", "slurmd"]  # the node's supervisor starts the new slurmd


def ordered(names: list[str]) -> list[str]:
    return sorted(names, key=lambda n: (ORDER.index(n) if n in ORDER else len(ORDER) + (n == "login"), n))


def _settled(name: str, to: str, runner, sleep=time.sleep, clock=time.monotonic) -> bool:
    check = {"slurmdbd": "slurmdbd", "slurmctld": "slurmctld", "slurmrestd": "slurmrestd"}.get(name, "slurmd")
    deadline = clock() + SETTLE_S
    while clock() < deadline:
        c = container(name)
        up = _call(runner, ["docker", "exec", c, "pgrep", "-x", check], 20).returncode == 0
        if up and (name != "slurmctld" or _call(runner, ["docker", "exec", c, "scontrol", "ping"], 20).returncode == 0):
            return True
        sleep(2)
    return False


def apply(do: dict, runner, say=print, sleep=time.sleep, clock=time.monotonic) -> str | None:
    """Upgrade the named daemons in SchedMD's order; None on success, else the first failure. Never recreates a container."""
    to = do["to"]
    if bad := ensure_image(to, runner, say):
        return bad
    r = _call(runner, ["docker", "create", image(to)], 60)
    if r.returncode:
        return f"could not read {image(to)}: {(r.stderr or r.stdout).strip()[-200:]}"
    tmp = r.stdout.strip()
    try:
        for name in ordered(do["daemons"]):
            before = version_in(name, runner)
            for cmd in swap_commands(name, tmp):
                r = _call(runner, cmd, COPY_S)
                if r.returncode:
                    return f"{name}: {' '.join(cmd[:3])} failed: {(r.stderr or r.stdout).strip()[-200:]}"
            if (cmd := restart_command(name)) and (r := _call(runner, cmd, 60)).returncode and cmd[3:4] != ["pkill"]:
                return f"{name}: restart failed: {(r.stderr or r.stdout).strip()[-200:]}"
            if restart_command(name) and not _settled(name, to, runner, sleep, clock):
                return f"{name}: not running again {SETTLE_S} s after the restart"
            now = version_in(name, runner)
            say(f"upgraded {name}: {before or '?'} -> {now or '?'}")
            if now != to:
                return f"{name} reports {now or 'nothing'} after the upgrade, wanted {to}"
    finally:
        _call(runner, ["docker", "rm", "-f", tmp], 30)
    return None
