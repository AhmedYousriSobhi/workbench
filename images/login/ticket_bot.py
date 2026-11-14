#!/usr/bin/env python3
"""A ticket bot: fake engineers working the alert tickets in jira-mock (issue 681, SPEC C-8). Runs on the login node:
`benchctl load on --tickets`. It talks only to the mock (http://jira-mock:8080), never to a real Jira.

Flow for each open ticket labelled `alert` (project OPS): some stay unassigned (--ignore, default 15%); the others are
assigned to an engineer, moved to In Progress, get a comment, and are Resolved after --resolve-after seconds. A share of
the resolved tickets (--reopen, default 10%) is reopened and worked again. One action every --every seconds on average
(Poisson gaps), scaled by the same daily rhythm as the user simulator when --day is given (issue 680). The governor's L1
flag pauses it. Ticket state is kept in memory, so a restart simply picks the open tickets up again."""
import argparse
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request

UA = "workbench-ticket-bot/1.0"
JIRA = "http://jira-mock:8080"
PAUSED = "/run/workbench-flags/arrivals.paused"  # the governor's L1 flag, as for the user simulator
RHYTHM_FLOOR = 0.1
ENGINEERS = ("bob", "carol", "dave", "erin")
COMMENTS = ("Looking into this now.", "Checked the node, nothing obvious yet.", "Restarted the service, watching it.",
            "Seen this before, same cause as last week.", "Vendor case opened, waiting on a reply.")
REOPEN_COMMENT = "Still happening after the fix, reopening."
MAX_TRACKED = 50  # tickets worked at once: bounded like the job queue
START, RESOLVE, REOPEN = "11", "31", "51"  # jira-mock transition ids (Start Progress, Resolve, Reopen)


def rhythm(hour: float, peak: float = 14, floor: float = RHYTHM_FLOOR) -> float:
    """Same curve as user_sim.rhythm (a test keeps them equal): 1 + 0.9 cos around the busiest hour, mean 1."""
    return max(floor, 1 + 0.9 * math.cos(2 * math.pi * (hour - peak) / 24))


def sim_hour(elapsed: float, day_s: float, start: float = 8) -> float:
    return (start + elapsed / day_s * 24) % 24 if day_s > 0 else start


class Bot:
    """The decisions, with no I/O: `step` returns (verb, key, argument) or None; `Client` carries it out."""

    def __init__(self, rng: random.Random, resolve_after: float = 600, ignore: float = 0.15, reopen: float = 0.10,
                 engineers: tuple[str, ...] = ENGINEERS) -> None:
        self.rng, self.resolve_after, self.ignore, self.reopen_share, self.engineers = rng, resolve_after, ignore, reopen, engineers
        self.stage: dict[str, str] = {}      # key -> assigned | started | commented | resolved | ignored
        self.since: dict[str, float] = {}    # key -> when it reached that stage

    def step(self, tickets: list[dict], now: float) -> tuple[str, str, str] | None:
        """`tickets`: [{key, status, assignee}] of the OPS alert tickets. One action, the oldest due work first."""
        seen = {t["key"]: t for t in tickets}
        for key in [k for k in self.stage if k not in seen]:
            self.stage.pop(key), self.since.pop(key, None)
        for key, stage in sorted(self.stage.items(), key=lambda kv: self.since[kv[0]]):
            t = seen[key]
            if stage == "assigned" and t["status"] == "Open":
                return self._go(key, "started", now, ("transition", key, START))
            if stage == "started" and t["status"] == "In Progress":
                return self._go(key, "commented", now, ("comment", key, self.rng.choice(COMMENTS)))
            if stage == "commented" and t["status"] == "In Progress" and now - self.since[key] >= self.resolve_after:
                return self._go(key, "resolved", now, ("transition", key, RESOLVE))
            if stage == "resolved" and t["status"] == "Resolved" and now - self.since[key] >= self.resolve_after / 2:
                if self.rng.random() < self.reopen_share:
                    return self._go(key, "assigned", now, ("transition", key, REOPEN))
                self.stage[key] = "done"
            if stage in ("assigned", "started", "commented", "resolved") and t["status"] not in ("Open", "In Progress", "Resolved"):
                self.stage[key] = "done"     # somebody else closed it
        fresh = [t for t in tickets if t["key"] not in self.stage and t["status"] == "Open"]
        if fresh and sum(s != "done" for s in self.stage.values()) < MAX_TRACKED:
            key = fresh[0]["key"]
            if self.rng.random() < self.ignore:
                self.stage[key], self.since[key] = "ignored", now      # nobody picks this one up
                return None
            who = self.rng.choice(self.engineers)
            return self._go(key, "assigned", now, ("assign", key, who))
        return None

    def _go(self, key: str, stage: str, now: float, action: tuple[str, str, str]) -> tuple[str, str, str]:
        self.stage[key], self.since[key] = stage, now
        return action


class Client:
    def __init__(self, base: str = JIRA) -> None:
        self.base = base

    def call(self, method: str, path: str, body: dict | None = None) -> dict | None:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"User-Agent": UA, **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except (urllib.error.HTTPError, OSError, ValueError):
            return None  # the mock is down, restarting or refused: try again at the next action

    def tickets(self) -> list[dict]:
        body = {"jql": 'project = OPS AND labels = alert AND status in (Open, "In Progress", Resolved) ORDER BY created',
                "maxResults": 100, "fields": ["status", "assignee"]}
        found = self.call("POST", "/rest/api/2/search", body) or {}
        return [{"key": i["key"], "status": i["fields"]["status"]["name"], "assignee": (i["fields"].get("assignee") or {}).get("name")}
                for i in found.get("issues", [])]

    def do(self, action: tuple[str, str, str]) -> None:
        verb, key, arg = action
        base = f"/rest/api/2/issue/{key}"
        if verb == "assign":
            self.call("PUT", f"{base}/assignee", {"name": arg})
        elif verb == "comment":
            self.call("POST", f"{base}/comment", {"body": arg})
        else:
            fields = {"resolution": {"name": "Done"}} if arg == RESOLVE else {}
            if arg == REOPEN:
                self.call("POST", f"{base}/comment", {"body": REOPEN_COMMENT})
            self.call("POST", f"{base}/transitions", {"transition": {"id": arg}, "fields": fields})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=120, help="mean seconds between actions")
    ap.add_argument("--resolve-after", type=float, default=600, help="seconds a ticket stays In Progress before it is resolved")
    ap.add_argument("--ignore", type=float, default=0.15, help="share of tickets nobody picks up")
    ap.add_argument("--reopen", type=float, default=0.10, help="share of resolved tickets that are reopened")
    ap.add_argument("--day", type=float, default=0, help="real seconds one simulated day lasts (0: no daily rhythm)")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    rng, client, start = random.Random(a.seed), Client(), time.monotonic()
    bot = Bot(rng, a.resolve_after, a.ignore, a.reopen)
    while True:
        hour = sim_hour(time.monotonic() - start, a.day) if a.day > 0 else None
        time.sleep(rng.expovariate((rhythm(hour) if hour is not None else 1) / a.every))
        if os.path.exists(PAUSED):
            continue
        action = bot.step(client.tickets(), time.monotonic())
        if action:
            client.do(action)


if __name__ == "__main__":
    sys.exit(main())
