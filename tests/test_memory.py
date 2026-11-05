"""benchctl/memory.py: the plan admits against measured use plus a tolerance, with the app under test on top (issue 287)."""
import math
from pathlib import Path

import yaml

from benchctl import checks, compose, memory, stack, workbench

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def catalog_keys() -> set[str]:
    keys = {memory.key_of(n) for group in compose.CATALOG.values() for n in group}
    return keys | set(workbench.OBSERVABILITY) | {"slurmd", "bmc-forwarder"}


def test_every_component_is_measured_or_listed_as_not_measured():
    """A new component must come with a measurement (or an explicit entry in NOT_MEASURED, expected at its cap)."""
    missing = catalog_keys() - set(memory.MEASURED_MB) - set(memory.NOT_MEASURED)
    assert not missing, f"add to memory.MEASURED_MB after a live run (docker stats): {sorted(missing)}"
    assert not set(memory.MEASURED_MB) & set(memory.NOT_MEASURED)
    assert set(memory.MEASURED_MB) <= catalog_keys()


def test_the_expected_use_covers_the_measurement_plus_the_tolerance_and_never_exceeds_the_cap():
    for name, group in ((n, g) for g in compose.CATALOG.values() for n in g):
        _, cap, _ = group[name]
        want = memory.expected_mb(name, cap)
        assert want <= cap
        seen = memory.MEASURED_MB.get(name)
        if seen is not None:
            assert want >= min(cap, math.ceil(seen * (1 + memory.TOLERANCE)))
            assert seen <= cap, f"{name} was seen using {seen} MB, above its cap {cap} MB"
    assert memory.expected_mb("cn001", 60, {"workbench.node": "cn001"}) == math.ceil(memory.MEASURED_MB["slurmd"] * 1.15)
    assert memory.expected_mb("bmc-cn001", 32) == math.ceil(memory.MEASURED_MB["bmc-forwarder"] * 1.15)
    assert memory.expected_mb("scenario-engine", 60) == 60  # unmeasured: the cap


def test_the_estimate_is_below_the_caps_and_the_app_is_a_separate_line(monkeypatch):
    prof = compose.load_profile("std")
    monkeypatch.setenv(memory.APP_ENV, "0")
    base = workbench.estimate(SIM, prof)
    assert 0 < base["expected_mb"] < base["total_mb"] and base["need_mb"] == base["expected_mb"]
    monkeypatch.delenv(memory.APP_ENV)
    est = workbench.estimate(SIM, prof)
    assert est["app_mb"] == memory.APP_MB == 1024 and est["need_mb"] == est["expected_mb"] + 1024
    monkeypatch.setenv(memory.APP_ENV, "1500")
    assert workbench.estimate(SIM, prof)["app_mb"] == 1500


def test_plan_prints_expected_and_cap_and_the_app_line(monkeypatch):
    monkeypatch.delenv(memory.APP_ENV)
    spec = workbench.defaults(SIM)
    text = workbench.render(spec, workbench.estimate(*workbench.effective(SIM, spec)))
    assert "expected" in text and "cap" in text and "app under test" in text and "1024" in text
    monkeypatch.setenv(memory.APP_ENV, "0")
    assert "app under test" not in workbench.render(spec, workbench.estimate(*workbench.effective(SIM, spec)))


def test_the_verdict_counts_the_app_and_says_how_to_leave_it_out(monkeypatch):
    monkeypatch.delenv(memory.APP_ENV)
    spec = workbench.defaults(SIM)
    est = workbench.estimate(*workbench.effective(SIM, spec))
    ok, msg = workbench.verdict(est, est["expected_mb"] + 1024 + 100)  # fits without the app, not with it
    assert not ok and "for the app" in msg and "--app-mb 0" in msg
    assert workbench.verdict({**est, "app_mb": 0, "need_mb": est["expected_mb"]}, est["expected_mb"] + 1024 + 100)[0]


def test_profile_budgets_are_the_expected_use_and_the_preflight_adds_the_app(monkeypatch):
    for f in (ROOT / "profiles").glob("*.yaml"):
        cfg = yaml.safe_load(f.read_text())
        monkeypatch.setenv(memory.APP_ENV, "0")
        est = workbench.estimate(SIM, compose.load_profile(cfg["name"]))["expected_mb"] / 1024
        assert est <= cfg["budget_gb"] <= est + 0.15, f"{f.name}: budget_gb {cfg['budget_gb']} but the expected use is {est:.2f} GB"
    monkeypatch.setenv(memory.APP_ENV, "4096")
    gb = 1024**3
    assert stack.admit("min", available=int(5.6 * gb))[0]          # 0.5 + 4 GB app + 1 GB floor
    assert not stack.admit("min", available=int(5.4 * gb))[0]
    monkeypatch.setenv(memory.APP_ENV, "0")
    assert stack.admit("min", available=int(1.6 * gb))[0]
    assert checks.SAFETY_FLOOR <= gb


def test_the_jira_real_numbers_are_the_measured_smallest_that_works():
    from benchctl import jira_real
    assert jira_real.LIMIT_MB >= memory.MEASURED_MB["jira-real"], "the cap is below what Jira was seen using"
    assert jira_real.HEAP_MAX_MB < jira_real.LIMIT_MB and jira_real.DB_LIMIT_MB >= memory.MEASURED_MB["jira-db"]
