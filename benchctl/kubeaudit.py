"""The Kubernetes API audit log as compact request records (issue 384, ADR 0087): who called, which verb, which object, what answer.

Read through `benchctl get kube-audit` by the governor (the page never touches Docker, SPEC W-3). Only what the narrow `AUDIT_POLICY`
in `benchctl/k8s.py` records exists here: nodes, node status, pods, pod status, pod binding and leases ("nodes and pods only"). Metadata
only: no request or response body is read, and nothing is written to disk."""
from collections.abc import Callable, Iterable

from . import capture

MAX_EVENTS = 500  # one read returns at most this many records (the newest): the answer, the governor's memory and the socket line stay bounded
MAX_WINDOW_S = 600  # the longest look-back a caller may ask for


def who(event: dict) -> str | None:
    """The user a request is for. A request made through Teleport's Kubernetes Service arrives as the `teleport` credential with the real
    user in `impersonatedUser` (ADR 0083): that is the user who asked, so it wins over `user`."""
    imp = (event.get("impersonatedUser") or {}).get("username")
    return imp or (event.get("user") or {}).get("username") or None


def compact(event: dict) -> dict | None:
    """One finished audit event as `{id, t, user, verb, resource, object, code}`; None for anything that is not a completed request."""
    if event.get("stage") != "ResponseComplete" or not event.get("auditID"):
        return None
    obj = event.get("objectRef") or {}
    res, sub, name, ns = obj.get("resource"), obj.get("subresource"), obj.get("name"), obj.get("namespace")
    t = capture._iso(event.get("stageTimestamp") or event.get("requestReceivedTimestamp"))
    if not (t and res and who(event)):
        return None
    return {"id": event["auditID"], "t": round(t, 3), "user": who(event), "verb": event.get("verb"),
            "resource": f"{res}/{sub}" if sub else res, "object": f"{ns + '/' if ns else ''}{name}" if name else None,
            "code": (event.get("responseStatus") or {}).get("code")}


def read(since: float, now: float, logs: Callable[[float], Iterable[dict]] = capture.audit_logs) -> list[dict]:
    """The records after `since` (unix seconds, never more than MAX_WINDOW_S back), oldest first, the newest MAX_EVENTS of them."""
    start = max(float(since), now - MAX_WINDOW_S)
    out: dict[str, dict] = {}
    for ev in logs(start):
        if (c := compact(ev)) and c["t"] > start:
            out[c["id"]] = c
    return sorted(out.values(), key=lambda c: c["t"])[-MAX_EVENTS:]
