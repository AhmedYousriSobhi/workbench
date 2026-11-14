#!/usr/bin/env python3
"""Writes the head node's fake CMDaemon events log, /var/spool/cmd/events.log (issue 714, ADR 0013 addendum).

bcm-mock keeps the node state changes (a scenario, the web page or a power call made them) and serves them as
cmevents.getEvents; this process, started by login.sh on the head node only, appends each one as a line

    Tue Oct  6 12:00:00 2026 [alert] cn001: Device status changed from UP to DOWN

(ctime-style date, `[severity]`, `host: text`). That is the shape a log reader expects from CMDaemon's messages; it is the
documented shape, not a capture of a real CMDaemon's file (no real CMDaemon was available). The file is rewritten when this
process starts, and followed from the last sequence number; a bcm-mock restart (its counter goes back) starts over.
"""
import contextlib
import importlib.machinery
import importlib.util
import os
import time

LOG = os.environ.get("WORKBENCH_EVENTS_LOG", "/var/spool/cmd/events.log")


def load_cmsh():
    """cmsh.py is installed as `cmsh` (no .py), so import it by path for its `call`."""
    for path in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "cmsh.py"), "/usr/local/bin/cmsh"):
        if os.path.exists(path):
            loader = importlib.machinery.SourceFileLoader("cmsh_mod", path)
            spec = importlib.util.spec_from_loader("cmsh_mod", loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod
    raise FileNotFoundError("cmsh")


def line(ev: dict) -> str:
    """One log line: `%a %b %e %H:%M:%S %Y` (day of month padded with a space, as ctime and CMDaemon print it)."""
    stamp = time.strftime("%a %b %e %H:%M:%S %Y", time.localtime(ev["time"]))
    return f"{stamp} [{ev['severity']}] {ev['host']}: {ev['text']}"


def append(path: str, events: list[dict]) -> None:
    if events:
        with open(path, "a") as f:
            f.write("".join(line(e) + "\n" for e in events))


def step(fetch, path: str, since: int) -> int:
    """One poll: append what is new, return the sequence to ask from next. A counter that went back means a restarted mock."""
    r = fetch(since)
    if r["seq"] < since:
        return step(fetch, path, 0)
    append(path, r["events"])
    return r["seq"]


def main() -> None:
    cmsh = load_cmsh()
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    open(LOG, "w").close()
    os.chmod(LOG, 0o644)
    since = 0
    while True:
        with contextlib.suppress(cmsh.Unreachable, OSError, KeyError):  # the mock is down or not started yet: try again
            since = step(lambda s: cmsh.call("cmevents", "getEvents", [s]), LOG, since)
        time.sleep(3)


if __name__ == "__main__":
    main()
