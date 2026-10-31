#!/usr/bin/env python3
"""Files a Jira ticket when Slurm takes a node out of service, like a bot watching sinfo (issue 160). Runs in slurmctld.

Polls `sinfo -N` every few seconds. A node that turns DOWN or DRAIN/DRAINING/FAIL is reported to the Jira mock's fault action
(kind "slurm", merged into the node's open ticket if it has one, so a Power off is one ticket, not two); a node that comes
back is reported as recovered. Does nothing when there is no Jira mock."""
import json
import os
import re
import subprocess
import time
import urllib.request

URL = os.environ.get("JIRA_FAULT_URL", "http://jira-mock:8080/sim/tickets")
EVERY = float(os.environ.get("NODE_WATCH_EVERY", "10"))
BAD = ("down", "drain", "drng", "fail")


def bad_nodes(text: str) -> dict[str, str]:
    """{node: 'state: reason'} for the nodes `sinfo -N -h -o '%N|%T|%E'` lists as out of service."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        name, _, rest = line.partition("|")
        state, _, reason = rest.partition("|")
        state = re.sub(r"[^a-z]+$", "", state.strip().lower())
        if name.strip() and state.startswith(BAD):
            out[name.strip()] = f"Slurm state {state}" + (f": {reason.strip()}" if reason.strip() not in ("", "none") else "")
    return out


def changes(before: dict[str, str], now: dict[str, str]) -> list[dict]:
    """The fault posts for what changed between two polls."""
    posts = [{"action": "fault", "node": n, "kind": "slurm", "detail": d, "merge": True} for n, d in now.items() if n not in before]
    posts += [{"action": "fault", "node": n, "kind": "slurm", "open": False, "detail": "Slurm has the node back in service."}
              for n in before if n not in now]
    return posts


def post(body: dict) -> None:
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def main() -> None:
    before: dict[str, str] | None = None
    while True:
        try:
            r = subprocess.run(["sinfo", "-N", "-h", "-o", "%N|%T|%E"], check=False, capture_output=True, text=True, timeout=10)
            now = bad_nodes(r.stdout) if r.returncode == 0 else before
            if now is not None:
                for body in changes(before or {}, now):
                    try:
                        post(body)
                    except OSError:
                        break  # no Jira (or it is down): the next change is tried again, this one is dropped
                before = now
        except (subprocess.SubprocessError, OSError):
            pass
        time.sleep(EVERY)


if __name__ == "__main__":
    main()
