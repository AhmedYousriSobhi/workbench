"""Slurm's own request statistics (#397): what `sdiag` says slurmctld served, plus whether slurmdbd answers (#400), as JSON for the page's Traffic view.

Read-only and argument-free: one `sdiag` on the login node, parsed here. A pure function over an injected runner, so it
unit-tests without Docker. Counters are totals since slurmctld started (or `sdiag --reset`, which nothing here calls).
"""

import re

from . import checks
from .scenario import LOGIN

RPC = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s+\(\s*\d+\)\s+count:(\d+)\s+ave_time:(\d+)")
THREADS = re.compile(r"Server thread count:\s*(\d+)")
TOP = 5


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
    return {**parse(r.stdout), "dbd": dbd(runner)}


def dbd(runner=checks.run) -> bool | None:
    """Does slurmdbd answer? One `sacctmgr` query on the login node: True answers, False no answer, None accounting is not configured."""
    r = runner(["docker", "exec", LOGIN, "timeout", "5", "sacctmgr", "-n", "show", "cluster", "format=cluster"])
    if "disabled" in f"{r.stdout}{r.stderr}".lower():
        return None
    return r.returncode == 0 and bool(r.stdout.strip())
