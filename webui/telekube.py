"""Kubernetes access through Teleport on the page (issue 755, ADR 0084 W3): Teleport's own request counters and the last `kube.request`
audit events. Read-only and local: the counters come from Teleport's diagnostics endpoint (the same one the status dot uses) and the
events from the audit log files in `compose/generated/teleport/data/log` (the folder the compose file mounts; the page has no Docker, SPEC W-3).
A counter Teleport does not publish yet is None ("not measured"), never 0."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from benchctl import teleportaudit

COUNTERS = {"requests": "teleport_kubernetes_server_api_requests_total",
            "exec": "teleport_kubernetes_server_exec_sessions_total",
            "portforward": "teleport_kubernetes_server_portforward_sessions_total"}
LOG_DIR = Path("compose/generated/teleport/data/log")
TAIL_BYTES = 256 * 1024   # the end of the newest files is enough for the last few calls
RECENT = 10
RECOVERY = "./scripts/benchctl up --with teleport --with k8s"


def counter(scrape: str, name: str) -> int | None:
    """The sum of every sample of `name` in a Prometheus text scrape; None when Teleport has not published it (not measured, not 0)."""
    found = [float(line.rsplit(" ", 1)[1]) for line in scrape.splitlines()
             if line.startswith(name) and line[len(name):len(name) + 1] in (" ", "{") and not line.startswith("#")]
    return int(sum(found)) if found else None


def _tail(path: Path) -> str:
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        data = f.read().decode(errors="replace")
    return data if size <= TAIL_BYTES else data.split("\n", 1)[-1]  # a cut first line is half an event


def recent_events(log_dir: Path = LOG_DIR, n: int = RECENT) -> list[dict]:
    """The last `n` kube.request events, newest first, as {time, user, verb, resource, code}."""
    # Teleport writes the day files under log/<cluster id>/YYYY-MM-DD.00:00:00.log; log/events.log is a link to today's with a container path, dangling on the host
    # (found live: the page showed no calls). A flat folder of day files still works (tests).
    files = sorted((f for f in [*log_dir.glob("*.log"), *log_dir.glob("*/*.log")] if f.is_file()), key=lambda f: f.name, reverse=True)[:2] if log_dir.is_dir() else []
    events: list[dict] = []
    for f in files:
        try:
            events = teleportaudit.select(teleportaudit.parse(_tail(f)), teleportaudit.KUBE_REQUEST) + events
        except OSError:
            continue
    out = []
    for ev in reversed(events[-n:]):
        kind, name, ns = ev.get("resource_kind"), ev.get("resource_name"), ev.get("resource_namespace")
        resource = "/".join(str(x) for x in (kind, name) if x) or str(ev.get("request_path") or "")
        out.append({"time": str(ev.get("time") or ""), "user": str(ev.get("user") or ""), "verb": str(ev.get("verb") or ""),
                    "resource": resource + (f" in {ns}" if ns else ""), "code": ev.get("response_code")})
    return out


def collect(built: bool, fetch: Callable[[str], object], base: str, log_dir: Path = LOG_DIR) -> dict:
    """What the Teleport details show for Kubernetes. `built`: the teleport and k8s parts are both in the saved workbench. Hidden (shown false)
    when they are not; one notice and no data when Teleport does not answer."""
    if not built:
        return {"shown": False}
    try:
        scrape = fetch(f"{base}/metrics")
        if not isinstance(scrape, str):
            raise TypeError("not a text scrape")
    except (OSError, ValueError, TypeError):
        return {"shown": True, "notice": f"Teleport is not answering, so its Kubernetes calls are not shown. Start it: {RECOVERY}",
                "counters": {}, "events": []}
    return {"shown": True, "notice": None, "counters": {k: counter(scrape, m) for k, m in COUNTERS.items()},
            "events": recent_events(log_dir)}


