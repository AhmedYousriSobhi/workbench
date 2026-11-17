"""Faults and scenario steps during a config lab replay (issue 554, owner decisions round 3, Q3). Standard library only, so the
scenario engine (and the web page, which plays scenarios through it) pay nothing for it.

A manual fault or a scenario on the cluster while a lab run replays is allowed, but recorded: one JSON line per action in the run's
`interference.jsonl` ({at, action, target, side, by}), and `lab_run.Run.comparable()` is then false, naming the first action. The
governor records its own node commands (`governor/labrun.py`); this records the scenario engine's steps. Best effort: a caller
that cannot write there (the page's restricted user) records nothing.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path("compose/generated/lab")  # benchctl.lab.ROOT
ACTIVE = "run-active.json"  # written by the governor: the run it supervises
INTERFERENCE = "interference.jsonl"
REPLAYING = ("replay_a", "replay_b")
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")  # benchctl.lab.NAME
_RUN = re.compile(r"\d{8}-\d{6}(-\d+)?")  # benchctl.lab_run.RUN_ID


def record(action: str, target: str | None, by: str, root: Path | None = None, now: Callable[[], float] = time.time) -> bool:
    """Append one line when a lab run is replaying; returns whether a line was written."""
    base = root or ROOT
    try:
        a = json.loads((base / ACTIVE).read_text())
        if not (_RUN.fullmatch(str(a["run"])) and _NAME.fullmatch(str(a["name"]))):
            return False
        folder = base / a["name"] / "reports" / a["run"]
        prog = json.loads((folder / "progress.json").read_text())
        if prog.get("state") not in REPLAYING:
            return False
        with (folder / INTERFERENCE).open("a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now())), "action": action, "target": target,
                                "side": prog.get("side"), "by": by}) + "\n")
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False
