"""The monitoring plane as Prometheus reports it (#397)."""

from webui import monitoring

TARGETS = {
    "data": {
        "activeTargets": [
            {
                "labels": {"job": "sim-nodes"},
                "scrapeUrl": "http://10.0.0.5:9400/metrics",
                "health": "up",
                "lastScrapeDuration": 0.004,
            },
            {
                "labels": {"job": "sim-nodes"},
                "scrapeUrl": "http://10.0.0.6:9400/metrics",
                "health": "down",
                "lastScrapeDuration": 0.002,
            },
            {
                "labels": {"job": "governor"},
                "scrapeUrl": "http://host:9401/metrics",
                "health": "up",
                "lastScrapeDuration": 0.01,
            },
        ]
    }
}


def fetcher(grafana=True):
    def fetch(url, *a):
        if "/api/v1/targets" in url:
            return TARGETS
        if grafana:
            return {"database": "ok"}
        raise OSError("down")

    return fetch


def test_each_scraped_job_is_a_link_with_prometheus_own_numbers():
    got = {link["to"]: link for link in monitoring.links(fetcher())}
    sim = got["sim-nodes"]
    assert (sim["from"], sim["port"], sim["targets"], sim["up"], sim["scrape_ms"]) == (
        "prometheus",
        9400,
        2,
        1,
        3.0,
    )
    assert sim["real"] is False and sim["measured"] is True and got["governor"]["real"] is True
    assert got["prometheus"]["from"] == "grafana" and got["prometheus"]["measured"] is False


def test_grafana_link_only_while_it_answers_and_nothing_without_prometheus():
    assert "prometheus" not in {link["to"] for link in monitoring.links(fetcher(grafana=False))}

    def down(url, *a):
        raise OSError("down")

    assert monitoring.links(down) == []
    assert monitoring.links(lambda url, *a: "not json") == []
