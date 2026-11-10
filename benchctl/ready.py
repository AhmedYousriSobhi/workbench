"""Readiness, not just "running" (issue 449): a stage is started only once the services it needs answer.

benchctl asks from outside once a container is running (`scontrol ping` through `compose exec`, an HTTP /healthz on a published
port). Nothing runs inside the containers afterwards. Docker healthchecks on the same services were timed against this on the
min profile and lost (12 s against 6 s for the whole start: a check only answers on its next tick, and it runs on a timer inside
the container's memory cap), so they were not kept.
"""
import time
import urllib.request
from collections.abc import Callable

HTTP_PORTS = {"bmc-farm": 8000, "bcm-mock": 8081, "jira-mock": 8080}  # published on 127.0.0.1 and served inside on the same port
CONTROLLER = "slurmctld"
CHECKED = [CONTROLLER, *HTTP_PORTS]


def http_ok(port: int, timeout: float = 2) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/healthz", headers={"X-Workbench-Probe": "1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def probe_ok(name: str, runner: Callable, dc: Callable, http: Callable[[int], bool] = http_ok) -> bool:
    """One outside check of one service."""
    if name == CONTROLLER:
        r = runner(dc("exec", "-T", name, "scontrol", "ping"))
        return r.returncode == 0 and " is UP" in r.stdout
    if name in HTTP_PORTS:
        return http(HTTP_PORTS[name])
    return True


def wait_ready(names: list[str], runner: Callable, dc: Callable, timeout: float = 120,
               sleep: Callable[[float], None] = time.sleep, http: Callable[[int], bool] = http_ok) -> list[str]:
    """The checked services among `names` still not answering after `timeout` seconds (empty when all do)."""
    pending, end = [n for n in names if n in CHECKED], time.monotonic() + timeout
    while pending:
        pending = [n for n in pending if not probe_ok(n, runner, dc, http)]
        if not pending or time.monotonic() >= end:
            break
        sleep(2)
    return pending
