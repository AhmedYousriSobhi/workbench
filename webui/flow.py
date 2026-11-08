"""Workload flow feed (issue #37): who is talking to which mock, for the dots on the Cluster Canvas.

Two real sources, no invented traffic:
- client traffic: every mock counts the requests it serves (`GET /sim/stats`), ignoring health checks, `/sim`
  control calls and requests that carry the X-Workbench-Probe header (this UI's own polling). A rise in a counter is
  traffic from "clients" (the app, Prometheus scrapes, curl) to that service.
- injections: scenario steps and fault buttons, noted by the code that applies them.
Slurm jobs are not here: the page has no Docker access (SPEC W-3), so it cannot read the scheduler.
"""
import threading
import time
from collections import deque

from .cluster import BCM, BMC, EXPORTER, JIRA, JIRA_REAL, TELEPORT, http_json

STATS = {"bmc-farm": f"{BMC}/sim/stats", "bcm-mock": f"{BCM}/sim/stats", "jira-mock": f"{JIRA}/sim/stats",
         "gpu-exporter": f"{EXPORTER}/sim/stats"}
# Teleport has no request counter of its own: its diagnostics endpoint counts every connection the proxy makes to a node
# (`tsh ssh`, the web terminal), which is the client traffic this page draws. Absent until the first connection.
TELEPORT_COUNTER = "teleport_connect_to_node_attempts_total"
COUNTERS = {"teleport": f"{TELEPORT}/metrics"}
# The real Jira publishes no request counter: only whether it answers (its /status), so its box shows up/down and says so.
REACH_ONLY = {"jira-real": f"{JIRA_REAL}/status", "k8s-api": "k8s-api"}  # the second is probed by benchctl.k8s.api_up (HTTPS with the cluster CA)
SCENARIO_TARGETS = {"bmc-farm": "bmc-farm", "bcm-mock": "bcm-mock", "gpu-exporter": "gpu-exporter",
                    "jira": "jira-mock"}  # scenario target name -> block; slurm, k8s, ticket-bot have no block
PAUSE_LEVEL = 3  # SPEC: the animation is a luxury; from L3 up the governor wants every MB and CPU cycle


def counter_total(scrape, name: str) -> int:
    """Sum of every sample of the Prometheus counter `name` in a text scrape; 0 while it has not been counted yet."""
    if not isinstance(scrape, str):
        raise TypeError("not a Prometheus text scrape")
    return int(sum(float(line.rsplit(" ", 1)[1]) for line in scrape.splitlines()
                   if line.startswith(name) and line[len(name):len(name) + 1] in (" ", "{")))


class Flow:
    def __init__(self, fetch=http_json, clock=time.monotonic, keep: int = 300, min_interval: float = 0.8):
        self.fetch, self.clock, self.min_interval = fetch, clock, min_interval
        self.events: deque = deque(maxlen=keep)
        self.seq = 0
        self.last: dict[str, int] = {}
        self.last_err: dict[str, int] = {}
        self.totals = dict.fromkeys([*STATS, *COUNTERS, *REACH_ONLY], 0)
        self.errors = dict.fromkeys(STATS, 0)  # only mocks that count error answers; Teleport, the real Jira, Slurm, k8s are "not measured"
        self.reachable = dict.fromkeys([*STATS, *COUNTERS, *REACH_ONLY], False)
        self.latency: dict[str, int] = {}  # ms the last probe took, per service that answered: measured, never estimated
        self.polled = float("-inf")
        self.lock = threading.Lock()

    def _add(self, src: str, dst: str, n: int, label: str, nodes: list[str] | None = None, err: int | None = None) -> None:
        self.seq += 1
        ev = {"id": self.seq, "from": src, "to": dst, "n": n, "label": label, "nodes": nodes or []}
        if err is not None:  # present only where a counter backs it
            ev["err"] = err
        self.events.append(ev)

    def note(self, target: str, label: str, nodes: list[str] | None = None) -> None:
        """A scenario step or UI fault action was applied to `target` (a scenario target name), to these nodes if any."""
        dst = SCENARIO_TARGETS.get(target)
        if dst:
            with self.lock:
                self._add("injector", dst, 1, label, [n for n in nodes or [] if isinstance(n, str)][:8])

    def poll(self) -> None:
        """Read every mock's request counter; a rise becomes events. Rate-limited, so many tabs cost one pass."""
        with self.lock:
            now = self.clock()
            if now - self.polled < self.min_interval:
                return
            self.polled = now
            for svc, url in {**STATS, **COUNTERS}.items():
                errs, t0 = None, self.clock()
                try:
                    if svc in COUNTERS:
                        got = counter_total(self.fetch(url), TELEPORT_COUNTER)
                    else:
                        stats = self.fetch(url)
                        got = int(stats["requests"])
                        errs = int(stats["errors"]) if "errors" in stats else None  # an older mock has no error counter: not measured
                except (OSError, ValueError, KeyError, TypeError):
                    self.last.pop(svc, None)
                    self.last_err.pop(svc, None)
                    self.reachable[svc] = False
                    self.latency.pop(svc, None)
                    continue
                self.reachable[svc] = True
                self.latency[svc] = round((self.clock() - t0) * 1000)
                prev = self.last.get(svc)
                self.last[svc] = got
                prev_err = self.last_err.get(svc)
                if errs is not None:
                    self.last_err[svc] = errs
                if prev is not None and got > prev:  # a restart makes got < prev: just re-baseline
                    self.totals[svc] += got - prev
                    bad = min(max(errs - prev_err, 0), got - prev) if errs is not None and prev_err is not None else None
                    if bad is not None:
                        self.errors[svc] += bad
                    self._add("clients", svc, got - prev, "requests", err=bad)
            for svc, url in REACH_ONLY.items():
                try:
                    if svc == "k8s-api":
                        from benchctl import k8s

                        t0 = self.clock()
                        self.reachable[svc] = k8s.api_up()
                        if self.reachable[svc]:
                            self.latency[svc] = round((self.clock() - t0) * 1000)
                        else:
                            self.latency.pop(svc, None)
                        continue
                    t0 = self.clock()
                    self.fetch(url)
                    self.reachable[svc] = True
                    self.latency[svc] = round((self.clock() - t0) * 1000)
                except (OSError, ValueError, KeyError, TypeError):
                    self.reachable[svc] = False
                    self.latency.pop(svc, None)

    def since(self, after: int) -> dict:
        with self.lock:
            return {"cursor": self.seq, "events": [e for e in self.events if e["id"] > after],
                    "totals": dict(self.totals), "errors": dict(self.errors), "latency": dict(self.latency), "reachable": dict(self.reachable)}
