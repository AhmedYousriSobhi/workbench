"""Alert and scheduler-load metrics of one lab side from Prometheus (issue 555, ADR 0050).

Only the built-in `ALERTS` series is read, so no metric name is guessed: for the side's time window, which alerts fired, how long
after the side started each first fired (in production-equivalent time) and how many times each one flapped (fired again after a
gap). `get` is injected (tests pass a fake). Prometheus not answering gives `None`, which the report lists as not measured; it never
changes the verdict.

Scheduler load (`side_sched`) reads slurmctld's own series that carry the side's `lab_run` label (ADR 0050 decision 9). The names are
those of Slurm 26.05.5's `metrics/openmetrics` plugin (`src/plugins/metrics/openmetrics/openmetrics.c`, the scheduler and its sdiag
statistics); cycle times are microseconds of the real controller, as in `sdiag` ("Last cycle"), so they are never multiplied by the
time factor. Only point-in-time gauges are read: the `*_max`, `*_mean` and `*_cnt` values count from slurmctld's last start or
sdiag reset, which can be before the side began, so they would mix the side with what came before it.
"""
from __future__ import annotations

import statistics
import urllib.parse
from collections.abc import Callable
from typing import Any

from . import alerts

QUERY = 'count by (alertname) (ALERTS{alertstate="firing"})'
STEP_S = 15
# gauge name -> what the report calls it; each is sampled over the side's window (max and median of the samples)
SCHED = {"slurm_schedule_cycle_last": "main scheduling cycle (us)", "slurm_bf_cycle_last": "backfill cycle (us)",
         "slurm_schedule_queue_len": "main scheduler queue (jobs)", "slurm_bf_queue_len": "backfill queue (jobs)",
         "slurm_server_thread_cnt": "slurmctld server threads", "slurm_agent_queue_size": "agent (RPC retry) queue",
         "slurm_slurmdbd_queue_size": "messages queued for slurmdbd"}


def _segments(points: list[tuple[float, float]], step: float) -> int:
    """Runs of consecutive firing samples: a gap of more than two steps ends one."""
    n, last = 0, None
    for t, v in points:
        if v <= 0:
            continue
        if last is None or t - last > 2 * step:
            n += 1
        last = t
    return n


def side_alerts(start: float, end: float, factor: float, get: Callable = alerts.http_json, step: float = STEP_S) -> dict[str, Any] | None:
    """{alerts: [{name, first_after_prod_s, flaps}], fired: N} for the window, or None when Prometheus does not answer."""
    url = f"{alerts.PROMETHEUS}/api/v1/query_range?" + urllib.parse.urlencode({"query": QUERY, "start": f"{start:.0f}", "end": f"{end:.0f}", "step": step})
    try:
        result = get(url)["data"]["result"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    out = []
    for series in result:
        pts = sorted((float(t), float(v)) for t, v in series.get("values", []))
        firing = [t for t, v in pts if v > 0]
        if not firing:
            continue
        out.append({"name": series.get("metric", {}).get("alertname", "-"), "first_after_prod_s": round((firing[0] - start) * factor, 1),
                    "flaps": max(_segments(pts, step) - 1, 0)})
    out.sort(key=lambda a: a["name"])
    return {"alerts": out, "fired": len(out)}


def sched_query(label: str) -> str:
    """Every scheduler gauge of one side: the `lab_run` label is `NAME/RUN/SIDE` (`lab_steps.prom_targets`)."""
    return '{{lab_run="{}",__name__=~"{}"}}'.format(label.replace("\\", "").replace('"', ""), "|".join(SCHED))


def side_sched(label: str, start: float, end: float, get: Callable = alerts.http_json, step: float = STEP_S) -> dict[str, Any] | None:
    """{metrics: {name: {what, max, median, samples}}} of one side's window; None when Prometheus does not answer or holds none of the
    side's series (then the report says "not measured", never 0)."""
    url = f"{alerts.PROMETHEUS}/api/v1/query_range?" + urllib.parse.urlencode({"query": sched_query(label), "start": f"{start:.0f}", "end": f"{end:.0f}", "step": step})
    try:
        result = get(url)["data"]["result"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    values: dict[str, list[float]] = {}
    for series in result:
        name = series.get("metric", {}).get("__name__", "")
        if name in SCHED:
            values.setdefault(name, []).extend(float(v) for _, v in series.get("values", []))
    out = {n: {"what": SCHED[n], "max": max(v), "median": statistics.median(v), "samples": len(v)} for n in SCHED if (v := values.get(n))}
    return {"metrics": out} if out else None
