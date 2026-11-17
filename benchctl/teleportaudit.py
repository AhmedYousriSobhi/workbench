"""Reading Teleport's audit log (issue 754, ADR 0084): the `dir` backend writes one JSON object per line, one file per day, under
`<data dir>/log/`, which the compose file mounts from `compose/generated/teleport/data`. The live Kubernetes test (and issue 763's
`tsh ssh` test) use these helpers to prove that a named user's kubectl call was audited.

The files belong to the user inside the container, so they are read with `docker exec` (the same path works whatever the host
user is); `read_log` says plainly when the folder is missing instead of returning nothing."""
import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

CONTAINER = "workbench-teleport-1"
LOG_DIR = "/var/lib/teleport/log"
KUBE_REQUEST = "kube.request"        # code T3009I in Teleport's audit event reference


class AuditError(Exception):
    pass


def read_log(runner: Callable = subprocess.run, container: str = CONTAINER, log_dir: str = LOG_DIR) -> str:
    """All audit lines as one text. Raises AuditError with the reason when the folder is not there."""
    chk = runner(["docker", "exec", container, "test", "-d", log_dir], capture_output=True, text=True, timeout=30, check=False)
    if chk.returncode != 0:
        raise AuditError(f"the Teleport audit log folder {log_dir} does not exist in {container}: "
                         "is the teleport part up, and is compose/generated/teleport/data mounted at /var/lib/teleport?")
    r = runner(["docker", "exec", container, "sh", "-c", f"find {log_dir} -type f -name '*.log' -exec cat {{}} +"],
               capture_output=True, text=True, timeout=60, check=False)
    if r.returncode != 0:
        raise AuditError(f"could not read the audit files in {container}: {r.stderr.strip()[-200:]}")
    return r.stdout


def parse(text: str) -> list[dict]:
    """The events in `text`; a line that is not a JSON object (a partial write at the end of a file) is skipped."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def _when(ev: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ev["time"]))
    except (KeyError, ValueError):
        return None


def select(events: list[dict], event: str = KUBE_REQUEST, since: datetime | None = None, **fields) -> list[dict]:
    """Events of one type at or after `since` whose fields equal `fields` (`response_code=403`; the code may be a number or a string)."""
    out = []
    for ev in events:
        if ev.get("event") != event:
            continue
        if since is not None and (t := _when(ev)) is not None and t < since:
            continue
        if all(str(ev.get(k)) == str(v) for k, v in fields.items()):
            out.append(ev)
    return out


def types_seen(events: list[dict]) -> list[str]:
    """Sorted distinct event types, to record which events a run produced (`exec`, `port`, `session.*` are not expected without a pod shell)."""
    return sorted({str(e.get("event")) for e in events if e.get("event")})


def now_minus(seconds: float = 5) -> datetime:
    """A start mark a little in the past, so a small clock difference between host and container does not hide the event."""
    return datetime.now(UTC) - timedelta(seconds=seconds)


def wait_for_events(find: Callable[[], list[dict]], timeout: float = 30, sleep: Callable[[float], None] | None = None,
                    clock: Callable[[], float] | None = None) -> list[dict]:
    """Poll `find` until it returns events (audit files are written a moment after the request) or `timeout` passes; the last result."""
    import time
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    end, got = clock() + timeout, []
    while True:
        got = find()
        if got or clock() >= end:
            return got
        sleep(1)
