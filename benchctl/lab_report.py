"""Config lab verdict, suggestions and report files (issue 555, ADR 0050).

`verdict(a, b, th)` follows ADR 0050: a hard failure is "Do not ship"; a new cost or a 5 to 20 % regression is "Ship with changes"; at
least one improvement over 5 % and nothing worse is "Ship"; everything within 5 % is "No effect". A partition below `min_samples`
started jobs on either side never causes a failure. A run that is not comparable gets no verdict. The thresholds are in
`sim.yaml lab.verdict` and are printed in every report. Suggestions come from a fixed catalogue; they are never generated text.
"""
from __future__ import annotations

from typing import Any

from . import lab_metrics

DEFAULT_THRESHOLDS = {"min_samples": 10, "pending_fail": 0.20, "wait_fail": 0.20, "regress": 0.05, "improve": 0.05, "wait_floor_s": 60.0}


def thresholds(sim: dict) -> dict[str, float]:
    return {**DEFAULT_THRESHOLDS, **((sim.get("lab") or {}).get("verdict") or {})}


def problems(sim: dict) -> list[str]:
    cfg = (sim.get("lab") or {}).get("verdict")
    if cfg is None:
        return []
    if not isinstance(cfg, dict):
        return ["lab.verdict must be a mapping"]
    out = [f"lab.verdict.{k} is not a known threshold ({', '.join(DEFAULT_THRESHOLDS)})" for k in cfg if k not in DEFAULT_THRESHOLDS]
    out += [f"lab.verdict.{k} must be a positive number" for k, v in cfg.items() if k in DEFAULT_THRESHOLDS and (isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0)]
    return out


def _change(a: float | None, b: float | None, floor: float) -> float | None:
    if a is None or b is None:
        return None
    return (b - a) / max(a, floor)


def compare(a: dict, b: dict, th: dict[str, float], factor: float, pairing: dict[str, Any]) -> dict[str, Any]:
    """The findings: lists of (what, change) for hard failures, regressions, costs and improvements."""
    floor = th["wait_floor_s"] / max(factor, 1.0)  # the floor is in production time; the metrics are in lab time
    hard: list[str] = []
    regress: list[str] = []
    cost: list[str] = []
    improve: list[str] = []
    if pairing["only_a"] or pairing["only_b"]:
        hard.append(f"lost jobs: {len(pairing['only_a'])} ran only in A, {len(pairing['only_b'])} only in B")
    pa, pb = a["pending"], b["pending"]
    if pb > pa and (pb - pa) / max(pa, 1) > th["pending_fail"]:
        hard.append(f"still pending up from {pa} to {pb}")
    elif pb < pa and (pa - pb) / max(pa, 1) > th["improve"]:
        improve.append(f"still pending down from {pa} to {pb}")
    for part in sorted(set(a["by_partition"]) & set(b["by_partition"])):
        x, y = a["by_partition"][part], b["by_partition"][part]
        if min(x["started"], y["started"]) < th["min_samples"]:
            continue
        c = _change(x["wait_median_s"], y["wait_median_s"], floor)
        if c is None:
            continue
        what = f"median wait in {part or '(none)'} {c:+.0%}"
        if c > th["wait_fail"]:
            hard.append(what)
        elif c > th["regress"]:
            regress.append(what)
        elif c < -th["improve"]:
            improve.append(what)
    if b["gpu_hours_lost"] > a["gpu_hours_lost"]:
        cost.append(f"GPU-hours lost to preemption up from {a['gpu_hours_lost']} to {b['gpu_hours_lost']}")
    if b["preempted"] > a["preempted"]:
        cost.append(f"preemptions up from {a['preempted']} to {b['preempted']}")
    return {"hard": hard, "regress": regress, "cost": cost, "improve": improve}


def verdict(findings: dict[str, list[str]], comparable: bool, ab: bool) -> str | None:
    if not ab:
        return None
    if not comparable:
        return "Not comparable"
    if findings["hard"]:
        return "Do not ship"
    if findings["regress"] or findings["cost"]:
        return "Ship with changes"
    if findings["improve"]:
        return "Ship"
    return "No effect"


# ---------------------------------------------------------------- suggestions (a fixed catalogue, never generated)

def suggestions(a: dict | None, b: dict, findings: dict[str, list[str]] | None) -> list[dict[str, str]]:
    out = []
    if b["preempted"] and (a is None or b["preempted"] > a["preempted"]):
        out.append({"id": "preemption-requeue", "text": f"{b['preempted']} job(s) were preempted ({b['gpu_hours_lost']} GPU-hours lost): check PreemptMode and the QOS preempt lists, or add a grace time"})
    if b["account_wait_spread"] is not None and b["account_wait_spread"] > 1.0:
        out.append({"id": "account-wait-spread", "text": "median wait differs a lot between accounts: review PriorityWeightFairshare and the accounts' fairshare shares"})
    if findings and any(s.startswith("still pending") for s in findings["hard"]):
        out.append({"id": "pending-up", "text": "more jobs stayed pending: review partition, QOS and per-user limits that the change touched"})
    if findings and any(s.startswith("median wait") for s in findings["hard"] + findings["regress"]):
        out.append({"id": "wait-regression", "text": "a partition waits longer: review PriorityTier, PriorityWeightPartition and the backfill settings (bf_*)"})
    return out


def build(meta: dict, a_rows: list[dict[str, str]] | None, b_rows: list[dict[str, str]], th: dict[str, float], pairing: dict[str, Any] | None,
          time_scaled: list[dict[str, str]]) -> dict[str, Any]:
    """The report document (report.json). `meta` carries run, seed, factor, length, comparable, paused and rejected counts."""
    f = float(meta["factor"])
    ab = a_rows is not None
    a = lab_metrics.side_metrics(a_rows, f) if ab else None
    b = lab_metrics.side_metrics(b_rows, f)
    findings = compare(a, b, th, f, pairing or {"only_a": [], "only_b": []}) if ab else None
    return {**meta, "single_run": True, "verdict": verdict(findings, bool(meta["comparable"]), ab) if findings else None, "findings": findings,
            "thresholds": th, "a": a, "b": b, "suggestions": suggestions(a, b, findings), "time_scaled": time_scaled,
            "not_measured": list(lab_metrics.NOT_MEASURED)}


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.0f}s"


def markdown(r: dict[str, Any]) -> str:
    L = [f"# Config lab report: {r['name']} run {r['run']}", ""]
    L.append(f"**Verdict: {r['verdict'] or 'none (candidate-only run: nothing to compare with)'}**" + (" (single run: repeat before relying on it)" if r["single_run"] else ""))
    L += ["", f"seed {r['seed']}, {r['jobs']} jobs, {r['target_hours']} h per side, time scaled x{r['factor']}, comparable: {'yes' if r['comparable'] else 'NO'}"]
    L.append(f"rejected by Slurm (no association, bad partition): {r['rejected'] or 'none'}")
    if r["findings"]:
        L += ["", "## Findings"]
        for k, title in (("hard", "Hard failures"), ("cost", "New costs"), ("regress", "Regressions"), ("improve", "Improvements")):
            for s in r["findings"][k]:
                L.append(f"- {title}: {s}")
        if not any(r["findings"].values()):
            L.append("- nothing outside the thresholds")
    L += ["", "## Wait by partition (production-equivalent time)", "", "| partition | side | jobs | pending | median | p95 |", "|---|---|---|---|---|---|"]
    for side in ("a", "b"):
        if r[side]:
            for part, s in r[side]["by_partition"].items():
                L.append(f"| {part or '(none)'} | {side.upper()} | {s['jobs']} | {s['pending']} | {_fmt(None if s['wait_median_s'] is None else s['wait_median_s'] * r['factor'])} | {_fmt(None if s['wait_p95_s'] is None else s['wait_p95_s'] * r['factor'])} |")
    L += ["", "## Preemption", ""] + [f"- {side.upper()}: {r[side]['preempted']} preempted, {r[side]['gpu_hours_lost']} GPU-hours lost of {r[side]['gpu_hours']}" for side in ("a", "b") if r[side]]
    if r["suggestions"]:
        L += ["", "## Suggestions"] + [f"- {s['text']}" for s in r["suggestions"]]
    L += ["", "## Thresholds", "", ", ".join(f"{k} {v}" for k, v in r["thresholds"].items())]
    if r["time_scaled"]:
        L += ["", "## Time-scaled parameters (the same factor on both sides)", ""] + [f"- {t.get('key')}: {t.get('from')} -> {t.get('to')}" for t in r["time_scaled"]]
    L += ["", "## Not measured in this version", "", ", ".join(r["not_measured"]), ""]
    return "\n".join(L)
