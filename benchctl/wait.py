"""`benchctl wait` (SPEC AG-7, ADR 0066): block until something is true, then say what was seen, in one call.

Exit 0 met, 1 timeout (or a scenario that ended failed), 2 precondition not met (the thing waited on cannot happen here, for
example `wait alert` when Prometheus does not run). It polls at one fixed, gentle interval and reads only what the matching
read verbs read.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from . import alerts, ready

MET, TIMEOUT, PRECONDITION, FAILED = "met", "timeout", "precondition", "failed"
EXIT = {MET: 0, TIMEOUT: 1, FAILED: 1, PRECONDITION: 2}
INTERVAL = 2.0
UI_TOKEN = Path("compose/generated/secrets/ui_token")


class PreconditionError(Exception):
    def __init__(self, message: str, code: str, fix: str):
        super().__init__(message)
        self.code, self.fix = code, fix


def poll(check: Callable[[], tuple[bool | None, dict]], timeout: float, interval: float = INTERVAL,
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> dict:
    """`check()` returns (True met | False keep waiting | None ended without success, what it saw); it may raise PreconditionError."""
    start = clock()
    seen: dict = {}
    while True:
        try:
            verdict, seen = check()
        except PreconditionError as e:
            return {"outcome": PRECONDITION, "ok": False, "elapsed_s": round(clock() - start, 1), "seen": {}, "code": e.code, "message": str(e), "fix": e.fix}
        if verdict is True:
            return {"outcome": MET, "ok": True, "elapsed_s": round(clock() - start, 1), "seen": seen}
        if verdict is None:
            return {"outcome": FAILED, "ok": False, "elapsed_s": round(clock() - start, 1), "seen": seen}
        if clock() - start >= timeout:
            return {"outcome": TIMEOUT, "ok": False, "elapsed_s": round(clock() - start, 1), "seen": seen}
        sleep(min(interval, timeout - (clock() - start)))


def ready_check(running: Callable[[], list[str]], answers: Callable[[str], bool]) -> Callable:
    def check():
        up = running()
        if not up:
            raise PreconditionError("no container of the workbench is running", "WB-STACK-NOT-RUNNING", "benchctl start")
        waiting = [n for n in up if n in ready.CHECKED and not answers(n)]
        return not waiting, {"running": len(up), "not_answering": waiting}
    return check


def node_state_check(rows: Callable[[], list[dict]], node: str, state: str) -> Callable:
    def check():
        now = next((r["state"] for r in rows() if r["name"] == node), None)
        return now == state, {"node": node, "state": now}
    return check


def alert_check(name: str, want: str, get: Callable = alerts.http_json) -> Callable:
    """`want` is firing or resolved."""
    def check():
        try:
            found = [a for a in alerts.fetch(get) if a["name"] == name]
        except alerts.NotRunning as e:
            raise PreconditionError(f"{e}: Prometheus is not running; start it with: {alerts.ENABLE}", "WB-PART-NOT-RUNNING", alerts.ENABLE) from e
        states = sorted({a["state"] for a in found})
        return (("firing" in states) if want == "firing" else not found), {"alert": name, "states": states}
    return check


def scenario_check(name: str, state: Callable[[], dict]) -> Callable:
    """The page plays one scenario at a time and keeps its run state; wait for NAME to end there."""
    def check():
        try:
            st = state()
        except (OSError, ValueError) as e:
            raise PreconditionError(f"the web page does not answer ({e}); scenarios waited on are the page's runs", "WB-PART-NOT-RUNNING", "benchctl start") from e
        seen = {"scenario": st.get("name"), "running": bool(st.get("running")), "ok": st.get("ok")}
        if st.get("name") != name or st.get("running"):
            return False, seen
        return (True if st.get("ok") else None), seen
    return check
