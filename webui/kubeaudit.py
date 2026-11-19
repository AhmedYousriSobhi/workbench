"""Per-user Kubernetes API request counter for the page (issue 384, ADR 0087).

The records come from the governor's `kube-audit` verb (SPEC W-3: this page never runs Docker): the API audit log, which the narrow
policy limits to nodes and pods ("nodes and pods only"). Here they are only counted: per user over the last minute, plus the last few
requests. Everything is bounded and in memory; nothing is written. "Not measured" is said, never shown as 0."""
import time
from collections import OrderedDict

SCOPE = "nodes and pods only"  # the audit policy records nothing else, so the counter says so (ADR 0087)
WINDOW_S = 60  # req/min is the count of the last minute
KEEP = 1000  # records held (a minute of a busy cluster); older ones are dropped
RECENT = 20  # the last requests listed
POLL_S = 5.0  # the governor is asked at most this often, however many tabs are open
RETRY_S = 15.0  # after a refusal or an error
OVERLAP_S = 2.0  # asked from a little before the newest record, so a late-written line is not lost (records are de-duplicated by id)


class KubeAudit:
    def __init__(self, call, clock=time.time):
        self.call, self.clock = call, clock  # call(request dict) -> the governor's answer
        self.events: OrderedDict[str, dict] = OrderedDict()
        self.cursor = 0.0
        self.next_at = float("-inf")
        self.measured = False
        self.note: str | None = None  # why it is not measured, one sentence

    def poll(self) -> None:
        now = self.clock()
        if now < self.next_at:
            return
        try:
            res = self.call({"cmd": "kube-audit", "since": max(self.cursor - OVERLAP_S, now - WINDOW_S, 0.0)})
        except (OSError, ValueError):
            res = {"ok": False, "error": "the governor is not reachable"}
        if not (isinstance(res, dict) and res.get("ok") and isinstance(res.get("events"), list)):
            self.measured, self.note = False, str((res or {}).get("error") or "not measured")[:160] if isinstance(res, dict) else "not measured"
            self.next_at = now + RETRY_S
            return
        self.measured, self.note, self.next_at = True, None, now + POLL_S
        for e in res["events"]:
            if isinstance(e, dict) and isinstance(e.get("id"), str) and isinstance(e.get("t"), (int, float)) and isinstance(e.get("user"), str):
                self.events[e["id"]] = {k: e.get(k) for k in ("t", "user", "verb", "resource", "object", "code")}
                self.cursor = max(self.cursor, float(e["t"]))
        while len(self.events) > KEEP:
            self.events.popitem(last=False)

    def summary(self) -> dict:
        """`{measured, scope, window_s, users: [{user, per_min}], recent: [...]}` (newest first); `note` says why it is not measured."""
        if not self.measured:
            return {"measured": False, "scope": SCOPE, "note": self.note, "users": [], "recent": []}
        cutoff, counts = self.clock() - WINDOW_S, {}
        for e in self.events.values():
            if e["t"] >= cutoff:
                counts[e["user"]] = counts.get(e["user"], 0) + 1
        recent = sorted(self.events.values(), key=lambda e: e["t"])[-RECENT:][::-1]
        return {"measured": True, "scope": SCOPE, "window_s": WINDOW_S, "note": None,
                "users": [{"user": u, "per_min": n} for u, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20]],
                "recent": [{"t": e["t"], "user": e["user"], "verb": e["verb"], "resource": e["resource"], "object": e["object"], "code": e["code"]} for e in recent]}
