"""Host-side ticket automation for either Jira (issue 284): the team that works the tickets, and the watcher that files them.

The mock does both inside itself (`/sim/activity`) and in slurmctld (`node_watch.py`). Neither can reach the real Jira, which
has no private endpoints, so the same two behaviours run here, on the host, through `benchctl.jira_driver` (standard REST calls
only; `--target mock` or `--target real` picks the Jira). The watcher reuses node_watch's own parsing, so the two cannot drift."""
from __future__ import annotations

import importlib.util
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .jira_driver import JiraDriver, JiraError

NODE_WATCH = Path(__file__).resolve().parent.parent / "images/slurm-ctl/node_watch.py"
SINFO = ["sinfo", "-N", "-h", "-o", "%N|%T|%E"]


def _node_watch():
    spec = importlib.util.spec_from_file_location("node_watch", NODE_WATCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def team(driver: JiraDriver, every: float = 45, count: int | None = None, say: Callable[[str], None] = print,
         sleep=time.sleep) -> int:
    """One change a team would make every `every` seconds (all of them if `count` is None). Returns how many were made."""
    made = 0
    while count is None or made < count:
        what = driver.tick()
        if what:
            made += 1
            say(what)
        elif count is not None:
            made += 1  # a quiet round counts, so a bounded run always ends
        sleep(every)
    return made


def sinfo(project: str) -> str | None:
    """sinfo output from the slurmctld container, or None when it cannot be read."""
    r = subprocess.run(["docker", "exec", f"{project}-slurmctld-1", *SINFO], check=False, capture_output=True, text=True, timeout=15)
    return r.stdout if r.returncode == 0 else None


def apply(driver: JiraDriver, before: dict[str, str], now: dict[str, str], watch) -> list[str]:
    """File or resolve tickets for what changed between two polls, with node_watch's own rules (kind slurm, merged)."""
    said = []
    for body in watch.changes(before, now):
        opened = body.get("open", True)
        out = driver.fault(body["node"], body["kind"], body["detail"], open=opened, merge=bool(body.get("merge")))
        said.append(f"{body['node']}: " + ("opened or updated " + str(out.get("created") or out.get("existing")) if opened
                                          else "resolved " + ", ".join(out["resolved"] or ["nothing"])))
    return said


def watch_nodes(driver: JiraDriver, project: str, every: float = 10, rounds: int | None = None,
                say: Callable[[str], None] = print, read=sinfo, sleep=time.sleep) -> None:
    """Poll slurmctld's `sinfo` like node_watch.py and keep the Jira in step. The first poll only records the state, as there."""
    watch = _node_watch()
    before: dict[str, str] | None = None
    n = 0
    while rounds is None or n < rounds:
        n += 1
        text = read(project)
        now = watch.bad_nodes(text) if text is not None else before
        if now is not None:
            try:
                for line in apply(driver, before or {}, now, watch):
                    say(line)
            except (JiraError, OSError) as e:
                say(f"Jira did not take it, trying again next round: {e}")
                sleep(every)
                continue
            before = now
        sleep(every)
