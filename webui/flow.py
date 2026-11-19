"""Workload flow feed (issue #37): who is talking to which mock, for the dots on the Cluster Canvas.

Two real sources, no invented traffic:
- client traffic: every mock counts the requests it serves (`GET /sim/stats`), ignoring health checks, `/sim`
  control calls and requests that carry the X-Workbench-Probe header (this UI's own polling). A rise in a counter is
  traffic from "clients" (the app, Prometheus scrapes, curl) to that service.
- injections: scenario steps and fault buttons, noted by the code that applies them.
- SSH sessions (issue 213, ADR 0088): the login and jump containers each run a small counter of the authenticated key logins sshd accepts
  (`images/login/ssh_counter.py`, `GET /sim/stats` on a localhost port). A rise is traffic on the direct SSH line (`ssh:NAME`, `ssh:jump`);
  a login seen from the jump host's address is the jump host's hop (`hop:NAME`), never a client line. No counter answering, no dots.
Slurm jobs are not here: the page has no Docker access (SPEC W-3), so it cannot read the scheduler.
"""
import threading
import time
from collections import deque

from .cluster import BCM, BMC, DCGM_REAL, EXPORTER, JIRA, JIRA_REAL, REAL_GPU, TELEPORT, http_json

STATS = {"bmc-farm": f"{BMC}/sim/stats", "bcm-mock": f"{BCM}/sim/stats", "jira-mock": f"{JIRA}/sim/stats",
         "gpu-exporter": f"{EXPORTER}/sim/stats"}
# Teleport has no request counter of its own: its diagnostics endpoint counts every connection the proxy makes to a node
# (`tsh ssh`, the web terminal), which is the client traffic this page draws. Absent until the first connection.
TELEPORT_COUNTER = "teleport_connect_to_node_attempts_total"
COUNTERS = {"teleport": f"{TELEPORT}/metrics"}
# The real Jira publishes no request counter: only whether it answers (its /status), so its box shows up/down and says so.
REACH_ONLY = {"jira-real": f"{JIRA_REAL}/status", "dcgm-exporter": f"{DCGM_REAL}/metrics", "real-gpu": f"{REAL_GPU}/metrics", "k8s-api": "k8s-api"}  # the second is probed by benchctl.k8s.api_up (HTTPS with the cluster CA)
SCENARIO_TARGETS = {"bmc-farm": "bmc-farm", "bcm-mock": "bcm-mock", "gpu-exporter": "gpu-exporter",
                    "jira": "jira-mock"}  # scenario target name -> block; slurm and k8s have no block
SSH_PREFIX, HOP_PREFIX = "ssh:", "hop:"  # flow keys of the SSH counters: a client's key login on a node, and the jump host's hop to a login node
PROBE_TIMEOUT = 0.2  # s: the mocks are on this machine; a frozen one (governor L3 freezes P2+) must not stall the feed for the others (issue 451)
BACKOFF_MAX = 10.0  # s: a service seen down is skipped for 1, 2, 4 ... up to this many seconds, and asked again after that
PAUSE_LEVEL = 3  # SPEC: the animation is a luxury; from L3 up the governor wants every MB and CPU cycle


def counter_total(scrape, name: str) -> int:
    """Sum of every sample of the Prometheus counter `name` in a text scrape; 0 while it has not been counted yet."""
    if not isinstance(scrape, str):
        raise TypeError("not a Prometheus text scrape")
    return int(sum(float(line.rsplit(" ", 1)[1]) for line in scrape.splitlines()
                   if line.startswith(name) and line[len(name):len(name) + 1] in (" ", "{")))


def ssh_sources() -> dict[str, str]:
    """{flow key: counter URL} of the SSH counters the saved selection has: `ssh:NAME` for each login node that takes a key (a Teleport-only
    node has no direct line) and `ssh:jump` when the jump host is part of it. Read from the saved selection (the page has no Docker, W-3)."""
    from benchctl import compose, controller

    from .cluster import saved_has_part, saved_logins

    def url(port: int) -> str:
        return f"http://127.0.0.1:{port + compose.COUNTER_OFFSET}/sim/stats"

    out = {f"{SSH_PREFIX}{lg['name']}": url(lg["port"]) for lg in saved_logins() if lg.get("port")}
    if saved_has_part("jump"):
        out[f"{SSH_PREFIX}jump"] = url(controller.JUMP_PORT)
    return out


def jump_address() -> str | None:
    try:
        from benchctl import addressing

        return addressing.service_ip("jump")
    except (ImportError, KeyError, ValueError):
        return None


class Flow:
    def __init__(self, fetch=None, clock=time.monotonic, keep: int = 300, min_interval: float = 0.8, ssh=None, jump_ip: str | None = None):
        self.fetch = fetch or (lambda url: http_json(url, timeout=PROBE_TIMEOUT))
        self.ssh_src = ssh or dict  # called on each poll: the login nodes come and go with the saved selection
        self.jump_ip = jump_ip
        self.last_src: dict[str, dict[str, int]] = {}  # per SSH counter: connections seen per source address at the last poll
        self.clock, self.min_interval = clock, min_interval
        self.fails: dict[str, int] = {}  # consecutive failed probes per service
        self.retry_at: dict[str, float] = {}  # a service seen down is not asked again before this time
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

    def _ssh_rise(self, svc: str, by_source: dict, baseline: bool) -> None:
        """One SSH counter's new connections since the last poll, split by who made them: the jump host's address is its hop to this login
        node (`hop:NAME`), any other address (the Docker gateway of the published port) is a client on the direct line."""
        prev, self.last_src[svc] = self.last_src.get(svc, {}), dict(by_source)
        if baseline:
            return
        direct = hop = 0
        for ip, n in by_source.items():
            d = n - prev.get(ip, 0)
            if d <= 0:
                continue
            if self.jump_ip and ip == self.jump_ip and svc != f"{SSH_PREFIX}jump":
                hop += d
            else:
                direct += d
        name = svc[len(SSH_PREFIX):]
        for key, src, n in ((svc, "clients", direct), (f"{HOP_PREFIX}{name}", "jump", hop)):
            if n:
                self.totals[key] = self.totals.get(key, 0) + n
                self._add(src, key, n, "connections")

    def _down(self, svc: str, now: float) -> None:
        self.fails[svc] = self.fails.get(svc, 0) + 1
        self.retry_at[svc] = now + min(BACKOFF_MAX, 2 ** (self.fails[svc] - 1))

    def _up(self, svc: str) -> None:
        self.fails.pop(svc, None)
        self.retry_at.pop(svc, None)

    def poll(self, force: bool = False) -> None:
        """Read every mock's request counter; a rise becomes events. Rate-limited, so many tabs cost one pass (`force`: a run's start
        and end read the counters now, issue 739)."""
        with self.lock:
            now = self.clock()
            if now - self.polled < self.min_interval and not force:
                return
            self.polled = now
            ssh = self.ssh_src()
            for svc in ssh:
                self.totals.setdefault(svc, 0)
                self.reachable.setdefault(svc, False)
            for svc in [k for k in self.reachable if k.startswith(SSH_PREFIX) and k not in ssh]:  # a login node left the selection
                for gone in (svc, HOP_PREFIX + svc[len(SSH_PREFIX):]):
                    for d in (self.totals, self.reachable, self.last, self.latency, self.last_src):
                        d.pop(gone, None)
            for svc, url in {**STATS, **COUNTERS, **ssh}.items():
                if now < self.retry_at.get(svc, float("-inf")):
                    continue  # seen down a moment ago: it keeps its "not reachable" state without costing a timeout
                errs, t0 = None, self.clock()
                try:
                    by_source = None
                    if svc in COUNTERS:
                        got = counter_total(self.fetch(url), TELEPORT_COUNTER)
                    elif svc in ssh:
                        stats = self.fetch(url)
                        got = int(stats["requests"])
                        by_source = {str(k): int(v) for k, v in stats.get("by_source", {}).items()}
                    else:
                        stats = self.fetch(url)
                        got = int(stats["requests"])
                        errs = int(stats["errors"]) if "errors" in stats else None  # an older mock has no error counter: not measured
                except (OSError, ValueError, KeyError, TypeError):
                    self.last.pop(svc, None)
                    self.last_err.pop(svc, None)
                    self.last_src.pop(svc, None)
                    self.reachable[svc] = False
                    self.latency.pop(svc, None)
                    self._down(svc, now)
                    continue
                self._up(svc)
                self.reachable[svc] = True
                self.latency[svc] = round((self.clock() - t0) * 1000)
                prev = self.last.get(svc)
                self.last[svc] = got
                if by_source is not None:  # an SSH counter: its own split into direct and hop events (a restart re-baselines)
                    self._ssh_rise(svc, by_source, prev is None or got < prev)
                    continue
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
                if now < self.retry_at.get(svc, float("-inf")):
                    continue
                try:
                    if svc == "k8s-api":
                        from benchctl import k8s

                        t0 = self.clock()
                        self.reachable[svc] = k8s.api_up()
                        if self.reachable[svc]:
                            self._up(svc)
                            self.latency[svc] = round((self.clock() - t0) * 1000)
                            n = k8s.api_requests()  # all callers, since the API server started: shown as a total, never as dots
                            if n is not None:
                                self.totals[svc] = n
                        else:
                            self.latency.pop(svc, None)
                            self._down(svc, now)
                        continue
                    t0 = self.clock()
                    self.fetch(url)
                    self._up(svc)
                    self.reachable[svc] = True
                    self.latency[svc] = round((self.clock() - t0) * 1000)
                except (OSError, ValueError, KeyError, TypeError):
                    self.reachable[svc] = False
                    self.latency.pop(svc, None)
                    self._down(svc, now)

    def counters(self) -> dict:
        """The running totals per service, with which ones answer (a scenario run keeps this at Play and at its end, issue 739)."""
        with self.lock:
            return {"totals": dict(self.totals), "errors": dict(self.errors), "reachable": dict(self.reachable)}

    def since(self, after: int) -> dict:
        with self.lock:
            return {"cursor": self.seq, "events": [e for e in self.events if e["id"] > after],
                    "totals": dict(self.totals), "errors": dict(self.errors), "latency": dict(self.latency), "reachable": dict(self.reachable)}
