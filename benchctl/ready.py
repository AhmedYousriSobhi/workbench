"""Readiness, not just "running" (issue 449): a stage is started only once the services it needs answer.

Two ways, chosen with WORKBENCH_READY, so they can be timed against each other:
- `probe` (default): benchctl asks from outside once a container is running (`scontrol ping` through `compose exec`, an HTTP
  /healthz on a published port). Nothing runs inside the containers afterwards.
- `healthcheck`: compose healthchecks on the same services; benchctl waits for Docker's `healthy`. The check runs in the container on a timer, inside its memory cap.
"""
import os
import time
import urllib.request
from collections.abc import Callable

MODE_ENV = "WORKBENCH_READY"
MODES = ("probe", "healthcheck")
HTTP_PORTS = {"bmc-farm": 8000, "bcm-mock": 8081, "jira-mock": 8080}  # published on 127.0.0.1 and served inside on the same port
CONTROLLER = "slurmctld"
CHECKED = [CONTROLLER, *HTTP_PORTS]


def mode() -> str:
    value = os.environ.get(MODE_ENV, "probe")
    return value if value in MODES else "probe"


def healthcheck_for(name: str) -> dict | None:
    """The compose healthcheck of a checked service (healthcheck mode), or None."""
    if name == CONTROLLER:
        test = ["CMD-SHELL", "scontrol ping | grep -q ' is UP'"]
    elif name in HTTP_PORTS:
        test = ["CMD", "python", "-c", f"import urllib.request as u; u.urlopen('http://127.0.0.1:{HTTP_PORTS[name]}/healthz', timeout=2)"]
    else:
        return None
    return {"test": test, "interval": "3s", "timeout": "3s", "retries": 40}


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


def healthy(runner: Callable, dc: Callable) -> set[str]:
    r = runner(dc("ps", "--format", "{{.Service}} {{.Health}}"))
    return {parts[0] for line in r.stdout.splitlines() if len(parts := line.split()) == 2 and parts[1] == "healthy"}


def wait_ready(names: list[str], runner: Callable, dc: Callable, timeout: float = 120,
               sleep: Callable[[float], None] = time.sleep, http: Callable[[int], bool] = http_ok,
               how: str | None = None) -> list[str]:
    """The checked services among `names` still not answering after `timeout` seconds (empty when all do)."""
    how = how or mode()
    pending, end = [n for n in names if n in CHECKED], time.monotonic() + timeout
    while pending:
        if how == "healthcheck":
            ok = healthy(runner, dc)
            pending = [n for n in pending if n not in ok]
        else:
            pending = [n for n in pending if not probe_ok(n, runner, dc, http)]
        if not pending or time.monotonic() >= end:
            break
        sleep(2)
    return pending
