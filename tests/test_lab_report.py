"""Config lab metrics, verdict, suggestions and report (issue 555). Synthetic accounting rows, no cluster."""
import pytest

from benchctl import lab_metrics, lab_report

TH = lab_report.DEFAULT_THRESHOLDS


def row(i, part="cpu", wait=10, state="COMPLETED", tres="cpu=4", account="a", run=100):
    sub = 1000 + i
    from datetime import datetime, timedelta
    t0 = datetime(2026, 1, 5)
    f = lambda s: (t0 + timedelta(seconds=s)).isoformat()
    pending = state == "PENDING"
    return {"JobName": f"j{i:04d}", "Partition": part, "QOS": "normal", "Account": account, "AllocTRES": tres, "State": state,
            "Submit": f(sub), "Eligible": f(sub), "Start": "Unknown" if pending else f(sub + wait), "End": "Unknown" if pending else f(sub + wait + run)}


def rows(n, **kw):
    return [row(i, **kw) for i in range(n)]


def test_percentile_and_gpu_parse():
    assert lab_metrics.percentile([1, 2, 3, 4], 50) == 2.5 and lab_metrics.percentile([], 50) is None
    assert lab_metrics.gpus("cpu=4,gres/gpu=2,gres/gpu:h100=2") == 4 and lab_metrics.gpus("cpu=4") == 0


def test_side_metrics_wait_pending_and_preemption_in_production_time():
    r = rows(10, wait=20) + [row(20, state="PENDING")] + [row(21, state="PREEMPTED", tres="gres/gpu=2", run=1800)]
    m = lab_metrics.side_metrics(r, factor=3.0)
    assert m["jobs"] == 12 and m["pending"] == 1 and m["preempted"] == 1
    assert m["wait_median_s"] == 20 and m["wait_median_prod_s"] == 60.0
    assert m["gpu_hours_lost"] == pytest.approx(2 * 1800 / 3600 * 3)
    assert m["by_partition"]["cpu"]["started"] == 11


def pairing(a=(), b=()):
    return {"only_a": list(a), "only_b": list(b)}


def report(a_rows, b_rows, comparable=True, ab=True, **kw):
    meta = {"name": "x", "run": "r", "seed": 1, "jobs": len(b_rows), "target_hours": 2, "factor": 1.0, "comparable": comparable, "rejected": {}, "paused_s": 0}
    return lab_report.build(meta, a_rows if ab else None, b_rows, {**TH, **kw}, pairing() if ab else None, [])


def test_no_effect_inside_five_percent():
    assert report(rows(20, wait=100), rows(20, wait=102))["verdict"] == "No effect"


def test_improvement_over_five_percent_is_ship():
    r = report(rows(20, wait=100), rows(20, wait=70))
    assert r["verdict"] == "Ship" and r["findings"]["improve"]


def test_regression_between_five_and_twenty_percent_is_ship_with_changes():
    assert report(rows(20, wait=100), rows(20, wait=112))["verdict"] == "Ship with changes"


def test_wait_up_over_twenty_percent_is_do_not_ship():
    r = report(rows(20, wait=100), rows(20, wait=150))
    assert r["verdict"] == "Do not ship" and "median wait in cpu" in r["findings"]["hard"][0]
    assert any(s["id"] == "wait-regression" for s in r["suggestions"])


def test_small_partition_never_fails_the_run():
    assert report(rows(5, wait=100), rows(5, wait=900))["verdict"] == "No effect"


def test_still_pending_up_is_a_hard_failure_and_a_lost_job_too():
    r = report(rows(20) + rows(1, state="PENDING"), rows(20) + [row(30 + i, state="PENDING") for i in range(3)])
    assert r["verdict"] == "Do not ship" and any("still pending" in s for s in r["findings"]["hard"])
    meta = report(rows(3), rows(3))
    assert meta["verdict"] == "No effect"
    f = lab_report.compare(meta["a"], meta["b"], TH, 1.0, pairing(a=["j0009"]))
    assert lab_report.verdict(f, True, True) == "Do not ship"


def test_new_preemption_cost_is_ship_with_changes_and_suggests():
    r = report(rows(20, wait=100), rows(20, wait=100) + [row(40, state="PREEMPTED", tres="gres/gpu=1")])
    assert r["verdict"] == "Ship with changes" and any(s["id"] == "preemption-requeue" for s in r["suggestions"])


def test_not_comparable_has_no_verdict_and_candidate_only_has_none():
    assert report(rows(20), rows(20), comparable=False)["verdict"] == "Not comparable"
    assert report(None, rows(20), ab=False)["verdict"] is None


def test_thresholds_come_from_sim_and_are_checked():
    assert lab_report.thresholds({"lab": {"verdict": {"wait_fail": 0.5}}})["wait_fail"] == 0.5
    assert lab_report.problems({"lab": {"verdict": {"nope": 1, "wait_fail": -1}}}) == [
        "lab.verdict.nope is not a known threshold (min_samples, pending_fail, wait_fail, regress, improve, wait_floor_s)", "lab.verdict.wait_fail must be a positive number"]
    assert lab_report.problems({}) == []


def test_markdown_prints_thresholds_scaling_and_what_was_not_measured():
    meta = {"name": "x", "run": "r", "seed": 1, "jobs": 5, "target_hours": 2, "factor": 3.0, "comparable": True, "rejected": {"b": 1}, "paused_s": 0}
    doc = lab_report.build(meta, rows(12), rows(12, wait=50), TH, pairing(), [{"key": "PriorityDecayHalfLife", "from": "7-0", "to": "2-8"}])
    md = lab_report.markdown(doc)
    assert "**Verdict:" in md and "## Thresholds" in md and "PriorityDecayHalfLife: 7-0 -> 2-8" in md and "alerts fired" in md and "x3.0" in md


# ---------------------------------------------------------------- alerts, repeats and the QOS table (issue 555)

def prom_answer(series):
    return lambda url: {"data": {"result": series}}


def test_side_alerts_reads_the_alerts_series_and_counts_flaps_in_production_time():
    from benchctl import lab_prom
    flap = {"metric": {"alertname": "NodeDrained"}, "values": [[100, "1"], [115, "1"], [400, "1"], [415, "1"], [700, "1"]]}
    once = {"metric": {"alertname": "NodeDown"}, "values": [[130, "1"], [145, "1"]]}
    got = lab_prom.side_alerts(100, 1000, 3.0, get=prom_answer([flap, once]))
    assert got["fired"] == 2
    assert got["alerts"][0] == {"name": "NodeDown", "first_after_prod_s": 90.0, "flaps": 0}
    assert got["alerts"][1] == {"name": "NodeDrained", "first_after_prod_s": 0.0, "flaps": 2}


def test_side_alerts_is_none_when_prometheus_does_not_answer_and_empty_when_nothing_fired():
    from benchctl import lab_prom

    def down(url):
        raise OSError("refused")
    assert lab_prom.side_alerts(0, 10, 1.0, get=down) is None
    assert lab_prom.side_alerts(0, 10, 1.0, get=prom_answer([])) == {"alerts": [], "fired": 0}


META = {"contract": "1", "name": "x", "run": "r2", "ab": True, "seed": 1, "jobs": 3, "target_hours": 2, "factor": 3.0, "length_s": 10,
        "paused_s": 0, "paused_side_s": {}, "comparable": True, "rejected": {}, "collected": {}}


def test_report_labels_repeats_and_lists_alerts_and_qos_and_drops_them_from_not_measured():
    a = b = rows(12)
    prom = {"a": {"alerts": [], "fired": 0}, "b": {"alerts": [{"name": "NodeDown", "first_after_prod_s": 30.0, "flaps": 1}], "fired": 1}}
    doc = lab_report.build(META, a, b, TH, {"only_a": [], "only_b": []}, [], earlier=["No effect"], prom=prom)
    assert doc["single_run"] is False and doc["repeat"] == {"runs": 2, "verdicts": ["No effect", "No effect"], "consistent": True}
    assert not any(n.startswith(("alerts fired", "alert flapping")) for n in doc["not_measured"]) and "scheduler load" in doc["not_measured"]
    md = lab_report.markdown(doc)
    assert "repeated: 2 runs" in md and "same verdict" in md and "## Wait by QOS" in md and "NodeDown first after 30s, 1 flap" in md
    assert "A: none fired" in md


def test_report_without_prometheus_or_earlier_runs_is_a_single_run_with_alerts_not_measured():
    doc = lab_report.build(META, rows(12), rows(12), TH, {"only_a": [], "only_b": []}, [], prom={"a": None, "b": None})
    assert doc["single_run"] is True and doc["alerts"] == {} and "alerts fired and time to alert" in doc["not_measured"]
    assert lab_report.repeats(["Ship", "Do not ship"])["consistent"] is False
