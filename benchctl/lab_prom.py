"""Alert metrics of one lab side from Prometheus (issue 555, ADR 0050).

Only the built-in `ALERTS` series is read, so no metric name is guessed: for the side's time window, which alerts fired, how long
after the side started each first fired (in production-equivalent time) and how many times each one flapped (fired again after a
gap). `get` is injected (tests pass a fake). Prometheus not answering gives `None`, which the report lists as not measured; it never
changes the verdict.
"""
from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from typing import Any

from . import alerts

QUERY = 'count by (alertname) (ALERTS{alertstate="firing"})'
STEP_S = 15


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
