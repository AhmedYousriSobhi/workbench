"""Request-level traffic for the Traffic analysis screen (redesign step 5, issue 501).

The three mocks record, per client request, only who asked (the User-Agent product token), the method and path shape, the status
class and the latency (`GET /sim/traffic`, components/*/). This reads them all into one answer. Nothing is invented: a mock that is
down or too old to record is said to be so ("services"), and the page shows "not measured" for it instead of a zero.
"""
import time

from .cluster import BCM, BMC, JIRA, http_json

SOURCES = {"bmc-farm": BMC, "bcm-mock": BCM, "jira-mock": JIRA}
TIMEOUT = 0.5  # s: the mocks are on this machine; a frozen one must not stall the others
RECENT_CAP = 400


def collect(since: float = 0.0, fetch=http_json) -> dict:
    """{"now", "edges", "services": {name: True | False | None}, "streams": [...], "recent": [[t, who, service, shape, code, ms], ...]}.

    `services`: True answered, False not reachable, None answered without a per-request record (an older mock image).
    `since` keeps only recent requests newer than that time (seconds since the epoch); streams are always cumulative."""
    out: dict = {"now": round(time.time(), 3), "edges": None, "services": {}, "streams": [], "recent": []}
    for svc, base in SOURCES.items():
        try:
            got = fetch(f"{base}/sim/traffic?since={since:.3f}", None, TIMEOUT)
        except (OSError, ValueError):
            out["services"][svc] = False
            continue
        if not isinstance(got, dict) or "streams" not in got:
            out["services"][svc] = None
            continue
        out["services"][svc] = True
        out["edges"] = out["edges"] or got.get("edges")
        out["streams"] += [{**s, "service": svc} for s in got["streams"]]
        out["recent"] += [[r[0], r[1], svc, r[2], r[3], r[4]] for r in got.get("recent", [])]
    out["recent"] = sorted(out["recent"])[-RECENT_CAP:]
    return out
