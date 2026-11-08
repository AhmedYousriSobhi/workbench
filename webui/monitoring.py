"""The monitoring plane as Prometheus itself reports it (#397): which targets it scrapes, how fast, and whether they answer.

Read-only HTTP to Prometheus (127.0.0.1:9090) and Grafana (127.0.0.1:3000), so the page needs no Docker access (W-3).
Every link here exists because Prometheus lists the target, and every number is Prometheus's own measurement: nothing is
declared or estimated. Nothing is returned while Prometheus does not answer.
"""

from urllib.parse import urlsplit

from .cluster import http_json

PROMETHEUS, GRAFANA = "http://127.0.0.1:9090", "http://127.0.0.1:3000"
SIMULATED_JOBS = {
    "sim-nodes"
}  # scraped targets that are the GPU exporter mock (observability/prometheus.yml); the governor's are real


def _link(frm: str, to: str, port: int, what: str, real: bool, **measured) -> dict:
    return {
        "id": f"{frm}>{to}:{port}",
        "from": frm,
        "to": to,
        "protocol": "HTTP",
        "port": port,
        "plane": "monitoring",
        "what": what,
        "real": real,
        "measured": bool(measured),
        "service": None,
        **measured,
    }


def links(fetch=http_json) -> list[dict]:
    try:
        targets = fetch(f"{PROMETHEUS}/api/v1/targets?state=active")["data"]["activeTargets"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    by: dict[tuple, list] = {}
    for t in targets:
        u = urlsplit(t.get("scrapeUrl", ""))
        by.setdefault((t.get("labels", {}).get("job", "?"), u.port or 80), []).append(t)
    out = [
        _link(
            "prometheus",
            job,
            port,
            f"scrape of {len(ts)} target{'s' * (len(ts) != 1)}",
            job not in SIMULATED_JOBS,
            up=sum(t.get("health") == "up" for t in ts),
            targets=len(ts),
            scrape_ms=round(
                sum(float(t.get("lastScrapeDuration", 0)) for t in ts) / len(ts) * 1000, 1
            ),
        )
        for (job, port), ts in sorted(by.items())
    ]
    try:
        fetch(f"{GRAFANA}/api/health")
        out.append(_link("grafana", "prometheus", 9090, "dashboard queries", True))
    except (OSError, ValueError):
        pass
    return out
