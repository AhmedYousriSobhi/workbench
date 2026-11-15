"""Config lab, trace side (issue 551, ADR 0050): stratified sampling, user mapping, time scaling, the concurrency estimate.

The trace is the `jobs` of lab_import.parse_trace. Nothing here submits a job (the replay does, a later issue); this decides
which jobs are replayed, under which user, how long they run in lab time and how many nodes keep the replay under the
running-jobs cap, so both runs of an A/B see exactly the same load.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from . import lab_slurm
from .lab_import import ImportError_ as LabError

_UNSET = ("", "unknown", "none", "n/a")


def parse_time(text: str) -> datetime | None:
    t = (text or "").strip()
    if t.lower() in _UNSET:
        return None
    try:
        return datetime.fromisoformat(t)
    except ValueError as e:
        raise LabError(f"sacct trace: bad time {text!r} (expected 2026-01-05T08:00:00)") from e


def elapsed_s(job: dict[str, str]) -> int:
    return lab_slurm.parse_duration(job.get("Elapsed", ""), "s") or 0


def interval(job: dict[str, str]) -> tuple[datetime, datetime] | None:
    """(start, end) of a job that ran; None for one that never started."""
    start = parse_time(job.get("Start", ""))
    if start is None:
        return None
    end = parse_time(job.get("End", "")) or start + timedelta(seconds=elapsed_s(job))
    return start, max(start, end)


def stratum(job: dict[str, str]) -> tuple[str, str, str]:
    return job.get("Account", ""), job.get("QOS", ""), job.get("Partition", "")


def sample(jobs: list[dict[str, str]], ratio: float, seed: int = 1) -> list[dict[str, str]]:
    """The same fraction of every (account, QOS, partition) stratum, at least one job each, original order kept. Deterministic for a seed."""
    if not 0 < ratio <= 1:
        raise LabError(f"sampling ratio must be above 0 and at most 1, not {ratio}")
    if ratio == 1:
        return list(jobs)
    strata: dict[tuple, list[int]] = {}
    for i, j in enumerate(jobs):
        strata.setdefault(stratum(j), []).append(i)
    keep: list[int] = []
    for key, idx in strata.items():
        n = min(len(idx), max(1, round(len(idx) * ratio)))
        keep += random.Random(f"{seed}:{key}").sample(idx, n)
    return [jobs[i] for i in sorted(keep)]


def map_users(jobs: list[dict[str, str]], keep: bool = False) -> dict[str, str]:
    """{production user: lab user}: lab-u001... in name order, or the names themselves with --keep-users."""
    users = sorted({j["User"] for j in jobs})
    if keep:
        return {u: u for u in users}
    width = max(3, len(str(len(users))))
    return {u: f"lab-u{i:0{width}d}" for i, u in enumerate(users, 1)}


def apply_users(jobs: list[dict[str, str]], mapping: dict[str, str]) -> list[dict[str, str]]:
    return [{**j, "User": mapping[j["User"]]} for j in jobs]


def span_s(jobs: list[dict[str, str]]) -> int:
    """Seconds from the first submit to the last end (or, for a job that never ran, its submit)."""
    subs = [parse_time(j["Submit"]) for j in jobs]
    subs = [s for s in subs if s]
    if not subs:
        raise LabError("sacct trace has no job with a Submit time")
    last = max([e for j in jobs if (iv := interval(j)) for e in (iv[1],)] + subs)
    return int((last - min(subs)).total_seconds())


def time_factor(jobs: list[dict[str, str]], target_hours: float = lab_slurm.DEFAULT_TARGET_HOURS) -> float:
    """The dilation factor: trace span over the target run length, never below 1 (a short trace is not stretched)."""
    if not lab_slurm.MIN_TARGET_HOURS <= target_hours <= lab_slurm.MAX_TARGET_HOURS:
        raise LabError(f"target length must be from {lab_slurm.MIN_TARGET_HOURS} to {lab_slurm.MAX_TARGET_HOURS} hours, not {target_hours}")
    return max(1.0, span_s(jobs) / (target_hours * 3600))


def scale_job(job: dict[str, str], t0: datetime, factor: float) -> dict[str, Any]:
    """A job in lab time: seconds after the run starts to submit it, how long it sleeps, its time limit (None when unlimited)."""
    sub = parse_time(job["Submit"])
    limit = lab_slurm.parse_duration(job.get("Timelimit", ""), "min")
    return {"submit_s": round(max(0.0, (sub - t0).total_seconds()) / factor, 1) if sub else 0.0,
            "run_s": lab_slurm.scale_seconds(elapsed_s(job), factor) if elapsed_s(job) else 0,
            "limit_s": lab_slurm.scale_seconds(limit, factor) if limit else None}


def peak_concurrency(jobs: list[dict[str, str]]) -> int:
    """The most jobs running at once (dilation moves every job by the same factor, so it does not change this)."""
    events: list[tuple[datetime, int]] = []
    for j in jobs:
        if iv := interval(j):
            events += [(iv[0], 1), (iv[1], -1)]
    now = peak = 0
    for _, d in sorted(events, key=lambda e: (e[0], e[1])):  # an end at the same instant as a start frees its slot first
        now += d
        peak = max(peak, now)
    return peak


def plan_load(nodes: list[dict[str, Any]], jobs: list[dict[str, str]], max_nodes: int, max_running_jobs: int, seed: int = 1) -> dict[str, Any]:
    """Downscale and sample together. When the sampled trace peaks above `max_running_jobs`, propose fewer lab nodes (both runs then see the
    same load; submissions are never throttled): returns the plan for the proposed size, `requested` and `proposed` node counts, `fits`."""
    n, requested = max_nodes, None
    for _ in range(16):
        scale = lab_slurm.downscale(nodes, n)
        requested = requested or scale["lab_nodes"]
        sampled = sample(jobs, scale["lab_nodes"] / scale["production_nodes"], seed)
        peak = peak_concurrency(sampled)
        floor = len(scale["groups"])
        if peak <= max_running_jobs or scale["lab_nodes"] <= floor:
            return {"scale": scale, "sampled": sampled, "peak": peak, "cap": max_running_jobs, "fits": peak <= max_running_jobs,
                    "requested_nodes": requested, "proposed_nodes": scale["lab_nodes"]}
        n = max(floor, min(scale["lab_nodes"] - 1, int(scale["lab_nodes"] * max_running_jobs / peak)))
    raise LabError("could not fit the replay under lab.max_running_jobs")  # pragma: no cover (the loop shrinks every pass)
