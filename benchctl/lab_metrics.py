"""Config lab metrics from one side's collected accounting (issue 555, ADR 0050). Pure functions over `sacct -P` rows; no cluster access.

Times are measured in lab seconds and shown in production-equivalent time (x the run's dilation factor). What accounting cannot say
(alerts, flapping, scheduler load, Kubernetes counts, idle-but-free GPUs, pending reasons) is listed in `NOT_MEASURED`, never guessed.
"""
from __future__ import annotations

import re
import statistics
from typing import Any

from . import lab_replay, lab_run

NOT_MEASURED = ("alerts fired and time to alert", "alert flapping", "scheduler load", "Kubernetes claim, quota and 429 counts",
                "idle-but-free GPUs", "pending-reason mix", "fairshare usage spread")
_GPU = re.compile(r"gres/gpu(?::[^=,]*)?=(\d+)")


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def gpus(tres: str) -> int:
    return sum(int(x) for x in _GPU.findall(tres or "")) or 0


def _secs(a, b) -> float | None:
    return (b - a).total_seconds() if a and b else None


def job_facts(row: dict[str, str]) -> dict[str, Any]:
    """One job: partition, QOS, account, wait (lab seconds, None while it never started), state kind, GPUs, run seconds."""
    state = lab_run.kind(row.get("State", ""))
    start, end = lab_replay.parse_time(row.get("Start", "")), lab_replay.parse_time(row.get("End", ""))
    ready = lab_replay.parse_time(row.get("Eligible", "")) or lab_replay.parse_time(row.get("Submit", ""))
    pending = (row.get("State", "").split()[:1] or [""])[0].upper() in ("PENDING", "") or start is None
    return {"partition": row.get("Partition", ""), "qos": row.get("QOS", ""), "account": row.get("Account", ""), "kind": state,
            "pending": pending, "wait": None if pending else _secs(ready, start), "gpus": gpus(row.get("AllocTRES", "")),
            "run": _secs(start, end) if start and end else None}


def _group(facts: list[dict], key: str) -> dict[str, dict[str, Any]]:
    out: dict[str, list[dict]] = {}
    for f in facts:
        out.setdefault(f[key], []).append(f)
    return {k: _stats(v) for k, v in sorted(out.items())}


def _stats(facts: list[dict]) -> dict[str, Any]:
    waits = [f["wait"] for f in facts if f["wait"] is not None]
    return {"jobs": len(facts), "started": len(waits), "pending": sum(1 for f in facts if f["pending"]),
            "wait_median_s": statistics.median(waits) if waits else None, "wait_p95_s": percentile(waits, 95)}


def side_metrics(rows: list[dict[str, str]], factor: float) -> dict[str, Any]:
    """The metrics of one side. `*_s` fields are lab seconds; `*_prod_s` the same x the dilation factor."""
    facts = [job_facts(r) for r in rows]
    total = _stats(facts)
    lost = sum((f["gpus"] * (f["run"] or 0)) for f in facts if f["kind"] == "preempted") / 3600
    by_account = {k: v["wait_median_s"] for k, v in _group(facts, "account").items() if v["wait_median_s"] is not None}
    spread = None
    if len(by_account) > 1:
        med = statistics.median(by_account.values())
        spread = (max(by_account.values()) - min(by_account.values())) / max(med, 1.0)
    out = {"jobs": len(facts), "pending": total["pending"], "wait_median_s": total["wait_median_s"], "wait_p95_s": total["wait_p95_s"],
           "by_partition": _group(facts, "partition"), "by_qos": _group(facts, "qos"),
           "preempted": sum(1 for f in facts if f["kind"] == "preempted"), "gpu_hours_lost": round(lost * factor, 3),
           "gpu_hours": round(sum(f["gpus"] * (f["run"] or 0) for f in facts) / 3600 * factor, 3), "account_wait_spread": spread,
           "kinds": {k: sum(1 for f in facts if f["kind"] == k) for k in sorted({f["kind"] for f in facts})}}
    for g in (out, *out["by_partition"].values(), *out["by_qos"].values()):
        for k in ("wait_median_s", "wait_p95_s"):
            g[k.replace("_s", "_prod_s")] = None if g.get(k) is None else round(g[k] * factor, 1)
    return out
