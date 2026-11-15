"""Slurm's own request statistics (#397): what `sdiag` says slurmctld served, plus whether slurmdbd answers (#400), as JSON for the page's Traffic view.

Read-only and argument-free: one `sdiag` on the login node, parsed here. A pure function over an injected runner, so it
unit-tests without Docker. Counters are totals since slurmctld started (or `sdiag --reset`, which nothing here calls).
"""

import re

from . import checks
from .scenario import LOGIN

NODE = re.compile(r"^([a-z][a-z0-9-]*[0-9]{3})\s+([a-z_]+)[*~#!%$@^+-]*(?:\s+(.*))?$")  # `sinfo -h -N -o "%N %T %E"`: name, state (a trailing flag char is dropped), reason ("none" when Slurm has none)
JOB = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)$")  # `squeue -h -o "%i %u %N"`: job id, user, node list (compressed: cn[001-003,005])
RANGE = re.compile(r"^(.*?)\[([^\]]+)\](.*)$")
RPC = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s+\(\s*\d+\)\s+count:(\d+)\s+ave_time:(\d+)")
THREADS = re.compile(r"Server thread count:\s*(\d+)")
TOP = 5
SERVING = {"idle", "allocated", "mixed"}  # states that are in service; any other wins when a node is listed twice


def parse(text: str) -> dict:
    """`{"rpcs": total count, "threads": n, "top": [{"type", "count", "ave_us"}...]}`; raises ValueError when it is not sdiag output."""
    rows = [(m[1], int(m[2]), int(m[3])) for ln in text.splitlines() if (m := RPC.match(ln))]
    t = THREADS.search(text)
    if not rows and not t:
        raise ValueError("not sdiag output")
    rows.sort(key=lambda r: -r[1])
    return {
        "rpcs": sum(r[1] for r in rows),
        "threads": int(t[1]) if t else None,
        "top": [{"type": n, "count": c, "ave_us": a} for n, c, a in rows[:TOP]],
    }


def collect(runner=checks.run) -> dict:
    r = runner(["docker", "exec", LOGIN, "sdiag"])
    if r.returncode:
        raise RuntimeError("sdiag: no answer from slurmctld")
    info = node_info(runner)
    return {
        **parse(r.stdout), "dbd": dbd(runner), "node_states": info[0] if info else None,
        "node_reasons": info[1] if info else None, "node_jobs": node_jobs(runner) if info else None,
    }


def dbd(runner=checks.run) -> bool | None:
    """Does slurmdbd answer? One `sacctmgr` query on the login node: True answers, False no answer, None accounting is not configured."""
    r = runner(["docker", "exec", LOGIN, "timeout", "5", "sacctmgr", "-n", "show", "cluster", "format=cluster"])
    if "disabled" in f"{r.stdout}{r.stderr}".lower():
        return None
    return r.returncode == 0 and bool(r.stdout.strip())


def node_info(runner=checks.run) -> tuple[dict[str, str], dict[str, str]] | None:
    """({node: Slurm state}, {node: reason}) from one `sinfo` on the login node (states idle, allocated, mixed, draining, drained, down...; a node
    has a reason only when Slurm gives one); None when it gives no answer. A node in two partitions is listed twice: the worse state wins (issue 414)."""
    r = runner(["docker", "exec", LOGIN, "sinfo", "-h", "-N", "-o", "%N %T %E"])
    if r.returncode:
        return None
    states: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for ln in r.stdout.splitlines():
        if (m := NODE.match(ln.strip())) and (m[1] not in states or m[2] not in SERVING):
            states[m[1]] = m[2]
            why = (m[3] or "").strip()
            if why and why.lower() != "none":
                reasons[m[1]] = why
            else:
                reasons.pop(m[1], None)
    return states, reasons


def node_states(runner=checks.run) -> dict[str, str] | None:
    """{node: Slurm state}; None when `sinfo` gives no answer."""
    info = node_info(runner)
    return info[0] if info else None


def expand(hosts: str) -> list[str]:
    """`cn[001-003,005],gpu001` -> the node names (one bracket group per name, as squeue prints them)."""
    out: list[str] = []
    for part in re.split(r",(?![^\[]*\])", hosts):
        m = RANGE.match(part)
        if not m:
            out.append(part)
            continue
        for item in m[2].split(","):
            lo, _, hi = item.partition("-")
            width = len(lo)
            if not lo.isdigit() or (hi and not hi.isdigit()):
                continue
            out += [f"{m[1]}{n:0{width}d}{m[3]}" for n in range(int(lo), int(hi or lo) + 1)]
    return out


def node_jobs(runner=checks.run) -> dict[str, list[dict[str, str]]] | None:
    """{node: [{"id", "user"}]} for the running jobs, from one `squeue` on the login node; None when it gives no answer."""
    r = runner(["docker", "exec", LOGIN, "squeue", "-h", "-t", "R", "-o", "%i %u %N"])
    if r.returncode:
        return None
    out: dict[str, list[dict[str, str]]] = {}
    for ln in r.stdout.splitlines():
        if m := JOB.match(ln.strip()):
            for node in expand(m[3]):
                out.setdefault(node, []).append({"id": m[1], "user": m[2]})
    return out
