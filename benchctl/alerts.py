"""Prometheus alerts, read back (`get alerts`, `wait alert`; ADR 0066).

The workbench's Prometheus (127.0.0.1:9090, part of every start) answers `GET /api/v1/alerts` with the alerts its rules are
firing or about to fire. The workbench ships seven default rules (`observability/rules.yml`: GpuXidCritical, GpuTempHigh, InfinibandPortDown, NodeDrained, NodeDown, ExporterDown, JiraErrors), so an empty list
means none is firing. When Prometheus does not answer, the part is not running, which is not the same as "no alerts".
"""
from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable

PROMETHEUS = "http://127.0.0.1:9090"
ENABLE = "benchctl start"  # Prometheus is part of every start (always: slurm, Prometheus, Grafana)


class NotRunning(Exception):
    """Prometheus does not answer."""


def http_json(url: str, timeout: float = 2.0):
    req = urllib.request.Request(url, headers={"X-Workbench-Probe": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch(get: Callable = http_json) -> list[dict]:
    """[{name, state, severity, labels, active_at}] for every pending or firing alert; raises NotRunning."""
    try:
        got = get(f"{PROMETHEUS}/api/v1/alerts")
        rows = got["data"]["alerts"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise NotRunning(f"Prometheus does not answer at {PROMETHEUS}") from e
    return [{"name": a.get("labels", {}).get("alertname", "-"), "state": a.get("state", "-"),
             "severity": a.get("labels", {}).get("severity", "-"), "labels": a.get("labels", {}), "active_at": a.get("activeAt", "-")}
            for a in rows]
