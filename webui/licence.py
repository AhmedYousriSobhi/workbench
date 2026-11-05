"""Detect the real Jira's test licence and renew it before it ends (issue 293).

The key counts from the start of Jira, so a restart gives another 3 hours. This reads the licence over HTTP only; the restart is
the governor's (`refresh-jira-licence`): the page has no Docker (SPEC W-3). Quiet while Jira is down or not set up.
"""
import asyncio
import time

from benchctl import checks, jira_real

COOLDOWN = 900  # seconds: a restart takes minutes, do not ask twice


async def watch(state: dict, socket: str, every: float = 60.0, rounds: int | None = None) -> None:
    """Keep state["text"] (one line for the Connections row) and renew when needed; state["asked"] is the last request's time."""
    n = 0
    while rounds is None or n < rounds:
        n += 1
        try:
            st = await asyncio.to_thread(jira_real.licence_status)
            state["text"] = jira_real.licence_text(st) if st else ""
            if jira_real.needs_refresh(st, jira_real.auto_minutes()):
                if time.time() - state.get("asked", 0.0) > COOLDOWN:
                    state["asked"] = time.time()
                    res = await asyncio.to_thread(checks.governor_request, {"cmd": "refresh-jira-licence"}, socket)
                    state["renewing"] = bool(res.get("ok") and not res.get("dry_run"))
                if state.get("renewing"):
                    state["text"] = "licence: renewing now (Jira restarts)"
            else:
                state["renewing"] = False
        except (OSError, ValueError, jira_real.JiraRealError):
            pass  # the next round tries again
        if rounds is None or n < rounds:
            await asyncio.sleep(every)
