"""`benchctl export test-report` (issue 739): the report builder, its parity with the page's `buildReport`, the run state that keeps the mocks'
counters, the command, and what the report must never hold. Hermetic: the page, Docker and Prometheus are faked; Node (when installed)
runs the page's own builder for the parity checks."""
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import jsonschema
import pytest
import yaml
from typer.testing import CliRunner

from benchctl import alerts, cli, contract, inventory, scenario, testreport, testwords

pytest.importorskip("starlette")
from webui.app import CLI_QUIET_S, Runs

sys.path.insert(0, str(Path(__file__).parent))
from test_step_words import SAMPLES, WITH_TOPOLOGY

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "webui/static/js/testrunmodel.js"
SCHEMA = json.loads((ROOT / "docs/agents/schemas/export-test-report.json").read_text())
runner = CliRunner()


def node_run(script: str, payload) -> list:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(payload), capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout)


def page_reports(cases: list[dict]) -> list[dict]:
    """The page's own builder over each case ({run, snap, base}): its data and Markdown."""
    js = (f"import {{ buildReport, eyeRows }} from {json.dumps(MODEL.as_uri())}; let s = ''; process.stdin.on('data', c => s += c).on('end', () => "
          "process.stdout.write(JSON.stringify(JSON.parse(s).map(c => { const r = buildReport({ preview: { name: c.run.name, ref: c.run.ref || '', steps: c.run.steps }, "
          "run: c.run, eye: eyeRows(c.snap, c.base, k => k) }); return { data: r.data, markdown: r.markdown }; }))));")
    return node_run(js, cases)


# ---- words: the same sentences as the page ----
def test_the_words_equal_the_pages_for_every_shipped_step_and_every_sample():
    steps = []
    for sc in scenario.list_all(ROOT / "scenarios", sim=WITH_TOPOLOGY):
        steps += [{"target": st.target, "do": st.do} for st in scenario.plan(sc)]
    steps += [{"target": t, "do": do} for t, do in SAMPLES]
    steps += [{"target": "nothing-known", "do": {"weird": 1}}, {"target": "slurm", "do": {"drain": ["cn001", "cn002"], "reason": "x"}}]
    assert len(steps) > 150
    js = (f"import {{ describe }} from {json.dumps(MODEL.as_uri())}; let s = ''; process.stdin.on('data', c => s += c)"
          ".on('end', () => process.stdout.write(JSON.stringify(JSON.parse(s).map(x => describe(x)))));")
    theirs = node_run(js, steps)
    assert [testwords.describe(s) for s in steps] == theirs


def test_the_expectation_words_equal_the_pages():
    labels = []
    for sc in scenario.list_all(ROOT / "scenarios", sim=WITH_TOPOLOGY):
        labels += [x["label"] for x in scenario.preview(sc, WITH_TOPOLOGY)["expects"]]
    kinds = sorted(set(re.findall(r'kind == "([a-z0-9_.]+)"', (ROOT / "benchctl/scenario.py").read_text())))
    args = ('{"node": "cn001", "state": "drain", "labels": ["a", "b"], "jql": "x", "status": "Done", "count": 1, "namespace": "ns", "name": "p", '
            '"reason": "Running", "selector": "app=x", "mount": "/scratch", "min_used_pct": 90, "quota_exceeded": "bob", "stale": false, "ready": false, "alive": false}')
    labels += [f"{k} {args}" for k in kinds] + ["jira.issue_count {\"jql\": \"y\", \"count\": 12, \"target\": \"real\"}", "weird.kind {\"a\": 1}", "plain text", "slurm.node_state {bad"]
    js = (f"import {{ expectWords }} from {json.dumps(MODEL.as_uri())}; let s = ''; process.stdin.on('data', c => s += c)"
          ".on('end', () => process.stdout.write(JSON.stringify(JSON.parse(s).map(x => expectWords(x)))));")
    assert len(labels) > 40
    assert [testwords.expect_words(x) for x in labels] == node_run(js, labels)


# ---- the report: the same data and Markdown as the page's ----
DETAILS = ["", "", "bmc-farm unreachable", "Slurm steps need the host CLI", "a | pipe", "plain words"]
TIMES = [0.2, 1.0, 2.5, 12.0, 0.05, None]


def run_state(sc: dict, k: int, **over) -> dict:
    """A run state as Runs holds it (webui/app.py `_fresh`), with results of every kind."""
    steps = []
    for i, s in enumerate(scenario.plan(sc)):
        status = ("ok", "skipped", "failed", "pending")[(i + k) % 4]
        steps.append({"at": s.at, "target": s.target, "do": s.do, "n": s.n, "page": not scenario.page_support(s), "why": scenario.page_support(s),
                      "status": status, "detail": DETAILS[(i + k) % len(DETAILS)] if status != "pending" else "", "t": TIMES[(i + k) % len(TIMES)] if status != "pending" else None})
    expects = [{"label": x["label"], "status": ("ok", "failed", "skipped")[(i + k) % 3], "detail": DETAILS[(i + k) % len(DETAILS)], "t": TIMES[(i + 2 * k) % len(TIMES)]}
               for i, x in enumerate(scenario.preview(sc, WITH_TOPOLOGY)["expects"])]
    return {"running": False, "name": sc["name"], "ref": sc.get("ref", ""), "speed": (1, 5, 0.5, 2.5, 10)[k % 5], "stopped": k % 4 == 3, "ok": k % 2 == 0,
            "started": 1763200000.123 + k, "elapsed": (42.5, 60.0, 0.4, 7.0)[k % 4], "source": "cli" if k % 3 == 0 else "page", "steps": steps, "expects": expects,
            "counters": {"base": None, "end": None}}


SNAPS = [
    ({"reachable": {"jira-mock": True, "bcm-mock": True, "k8s-api": True, "dead": False}, "totals": {"jira-mock": 40, "bcm-mock": 3, "dead": 0}, "errors": {"jira-mock": 5, "bcm-mock": 0}},
     {"totals": {"jira-mock": 25, "bcm-mock": 3, "dead": 0}, "errors": {"jira-mock": 3, "bcm-mock": 0}}),
    ({"reachable": {"jira-mock": True}, "totals": {"jira-mock": 7}}, None),
    ({"reachable": {"a": True, "b": True}, "totals": {"a": 0}, "errors": {}}, {"totals": {"a": 0}, "errors": {}}),
    (None, None),
]


def test_the_report_equals_the_pages_for_every_shipped_scenario():
    cases, mine = [], []
    for k, sc in enumerate(scenario.list_all(ROOT / "scenarios", sim=WITH_TOPOLOGY)):
        run = run_state(sc, k)
        snap, base = SNAPS[k % len(SNAPS)]
        cases.append({"run": run, "snap": snap, "base": base})
        mine.append(testreport.build(run, snap, base))
    theirs = page_reports(cases)
    assert len(cases) > 20
    for (data, md), want, case in zip(mine, theirs, cases, strict=True):
        assert json.loads(json.dumps(data)) == want["data"], case["run"]["name"]
        assert md == want["markdown"], case["run"]["name"]


def test_a_run_still_going_and_an_unfinished_one_read_the_same_as_the_page():
    sc = scenario.list_all(ROOT / "scenarios", sim=WITH_TOPOLOGY)[0]
    running = {**run_state(sc, 1), "running": True, "elapsed": 3.0}
    lost = {**run_state(sc, 2), "stopped": True, "lost": True}
    no_start = {k: v for k, v in run_state(sc, 4).items() if k != "started"}
    cases = [{"run": r, "snap": None, "base": None} for r in (running, lost, no_start)]
    for (data, md), want in zip((testreport.build(c["run"], None, None) for c in cases), page_reports(cases), strict=True):
        assert json.loads(json.dumps(data)) == want["data"]
        assert md == want["markdown"]


def test_to_fixed_and_numbers_follow_javascript():
    assert [testreport.to_fixed0(x) for x in (0, 2.5, 3.5, 4.4, 59.5)] == ["0", "3", "4", "4", "60"]
    assert [testwords.js(x) for x in (12.0, 0.2, 5, True, None, "x", [1.0, "a"])] == ["12", "0.2", "5", "true", "undefined", "x", "1,a"]
    assert testreport.iso(1763200041.9) == "2025-11-15T09:47:21Z"


# ---- the run state keeps the mocks' counters, with no browser open ----
class Clock:
    now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def scen(tmp_path):
    d = tmp_path / "scenarios"
    d.mkdir()
    (d / "quick.yaml").write_text("name: quick\nref: SC-99\ndescription: d\nsteps:\n  - {at: 0s, target: dcgm, do: {stop: true}}\n")
    return d


def counters_seq():
    """A counter reader that returns a higher number every time it is asked."""
    n = {"i": 0}

    def read():
        n["i"] += 1
        return {"totals": {"jira-mock": n["i"] * 10}, "errors": {"jira-mock": n["i"]}, "reachable": {"jira-mock": True}}
    return read


def test_a_page_run_keeps_the_counters_at_play_and_at_the_end(scen):
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    runs = Runs(scen, sim, None, counters=counters_seq())
    runs.start("quick", 1.0)
    for _ in range(100):
        if not runs.snapshot()["running"]:
            break
        time.sleep(0.05)
    st = runs.snapshot()
    assert st["running"] is False and st["ref"] == "SC-99" and st["steps"][0]["why"].startswith("dcgm steps need the host CLI")
    assert st["counters"]["base"]["totals"] == {"jira-mock": 10} and st["counters"]["end"]["totals"] == {"jira-mock": 20}


def test_a_cli_run_keeps_them_too_at_its_end_and_when_it_is_lost(scen):
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    key = scenario.plan_key(scenario.plan(yaml.safe_load((scen / "quick.yaml").read_text())))
    clock = Clock()
    runs = Runs(scen, sim, None, clock=clock, counters=counters_seq())
    run = runs.cli_event({"event": "start", "name": "quick", "speed": 1, "key": key})["run"]
    assert runs.snapshot()["counters"] == {"base": {"totals": {"jira-mock": 10}, "errors": {"jira-mock": 1}, "reachable": {"jira-mock": True}}, "end": None}
    runs.cli_event({"event": "end", "run": run, "ok": True})
    st = runs.snapshot()
    assert st["running"] is False and st["counters"]["end"]["totals"] == {"jira-mock": 20}
    run = runs.cli_event({"event": "start", "name": "quick", "speed": 1, "key": key})["run"]
    clock.now += CLI_QUIET_S + 1
    lost = runs.snapshot()
    assert lost["lost"] is True and lost["counters"]["base"]["totals"] == {"jira-mock": 30} and lost["counters"]["end"]["totals"] == {"jira-mock": 40}


def test_a_failing_counter_never_leaves_a_run_running(scen):
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())

    def boom():
        raise OSError("a mock hung")
    runs = Runs(scen, sim, None, counters=boom)
    runs.start("quick", 1.0)
    for _ in range(100):
        if not runs.snapshot()["running"]:
            break
        time.sleep(0.05)
    st = runs.snapshot()
    assert st["running"] is False and st["counters"] == {"base": None, "end": None}


def test_without_a_counter_reader_a_run_keeps_none(scen):
    runs = Runs(scen, yaml.safe_load((ROOT / "sim.yaml").read_text()), None)
    assert runs._count() is None


# ---- the command ----
@pytest.fixture
def finished():
    sc = {"name": "node-down", "ref": "SC-01", "steps": [{"at": 0, "target": "bmc-farm", "do": {"nodes": ["cn001"], "temp": 90}},
                                                        {"at": 5, "target": "ssh", "do": {"state": "down"}}]}
    run = run_state(sc, 0)
    run.update(source="page", stopped=False, ok=True, steps=[{**s, "status": "ok", "detail": "", "t": 0.5 * i} for i, s in enumerate(run["steps"])],
               expects=[{"label": 'slurm.node_state {"node": "cn001", "state": "drain"}', "status": "ok", "detail": "", "t": 8.0}])
    run["counters"] = {"base": {"totals": {"jira-mock": 1, "bcm-mock": 0}, "errors": {"jira-mock": 0}},
                       "end": {"totals": {"jira-mock": 6, "bcm-mock": 0}, "errors": {"jira-mock": 2}, "reachable": {"jira-mock": True, "bcm-mock": True, "k8s-api": True}}}
    return run


@pytest.fixture
def cluster_now(monkeypatch):
    monkeypatch.setattr(inventory, "_containers", lambda runner=None: {"cn001": "exited", "cn002": "running", "gpu001": "running"})
    monkeypatch.setattr(cli, "_fault_rows", lambda: [{"target": "cn001", "kind": "compute", "state": "down", "label": "powered off"}])
    monkeypatch.setattr(alerts, "fetch", lambda *a, **k: [{"name": "NodeDown", "state": "firing", "severity": "critical", "labels": {"instance": "10.141.0.5:9100"}, "active_at": "t"}])


def invoke(monkeypatch, state, *args):
    monkeypatch.setattr(cli, "_page_run_state", lambda: state)
    return runner.invoke(cli.app, ["export", "test-report", *args])


def test_markdown_by_default_with_the_page_report_and_an_at_export_part(monkeypatch, finished, cluster_now):
    r = invoke(monkeypatch, finished)
    assert r.exit_code == 0, r.output
    out = r.stdout
    assert out.startswith("# Test run: node-down (SC-01)\n") and "- Result: **passed**" in out
    assert "| 0 s | 0 s | bmc-farm | " in out and "- ok at 8 s: Slurm shows cn001 as drained" in out
    assert "| jira-mock | 5 | 2 | called |" in out
    after = out.split("## At export")[1]
    assert "1 exited, 2 running" in after and "cn001 (compute): down, powered off" in after and "NodeDown (critical): firing" in after
    assert "10.141" not in out


def test_json_is_the_same_data_with_a_contract_version_and_matches_its_schema(monkeypatch, finished, cluster_now):
    data = json.loads(invoke(monkeypatch, finished, "last", "--json").stdout)
    jsonschema.validate(data, SCHEMA, cls=jsonschema.Draft202012Validator)
    assert data["contract_version"] == contract.CONTRACT_VERSION and data["scenario"] == "node-down" and data["verdict"] == "passed"
    assert {k: v for k, v in data["at_export"]["nodes"].items() if k != "absent"} == {"exited": 1, "running": 2} and data["at_export"]["alerts"] == [{"name": "NodeDown", "state": "firing", "severity": "critical"}]
    assert [e["service"] for e in data["clients_eye"]] == ["bcm-mock", "jira-mock", "k8s-api"]
    md = invoke(monkeypatch, finished, "last").stdout
    assert "## What was injected" in md and md.count("node-down") >= 1


def test_the_name_or_the_reference_picks_the_run_and_o_writes_a_file(monkeypatch, finished, cluster_now, tmp_path):
    out = tmp_path / "r.md"
    r = invoke(monkeypatch, finished, "SC-01", "-o", str(out))
    assert r.exit_code == 0 and "wrote" in r.stdout and out.read_text().startswith("# Test run: node-down")
    assert invoke(monkeypatch, finished, "NODE-down", "-o", "-").stdout.startswith("# Test run")


@pytest.mark.parametrize(("state", "args", "words"), [
    ({"running": False}, [], "the page has no scenario run"),
    ({"running": False, "name": None}, ["last"], "benchctl scenario run NAME"),
    ({"running": False, "name": "node-down", "ref": "SC-01"}, ["other"], "the page's last run is 'node-down'"),
    ({"running": True, "name": "node-down"}, [], "benchctl wait scenario node-down"),
])
def test_nothing_to_export_is_one_message_and_exit_1(monkeypatch, cluster_now, state, args, words):
    r = invoke(monkeypatch, state, *args)
    assert r.exit_code == 1 and r.stdout == "" and words in r.stderr and len(r.stderr.strip().splitlines()) == 1


def test_in_agent_mode_the_message_is_one_coded_line(monkeypatch, cluster_now):
    monkeypatch.setenv("BENCHCTL_AGENT", "1")
    r = invoke(monkeypatch, {"running": False})
    assert r.exit_code == 1 and r.stderr.startswith("WB-NO-RUN: nothing to export") and "| fix: benchctl scenario run NAME" in r.stderr


def test_a_page_that_does_not_answer_is_one_message(monkeypatch):
    def down():
        raise OSError("connection refused")
    monkeypatch.setattr(cli, "_page_run_state", down)
    r = runner.invoke(cli.app, ["export", "test-report"])
    assert r.exit_code == 1 and "the web page does not answer" in r.stderr and "benchctl start" in r.stderr


def test_what_cannot_be_read_is_not_measured_never_an_empty_list(monkeypatch, finished):
    monkeypatch.setattr(inventory, "_containers", lambda runner=None: (_ for _ in ()).throw(OSError("no docker")))
    monkeypatch.setattr(cli, "_fault_rows", lambda: (_ for _ in ()).throw(OSError("no docker")))
    monkeypatch.setattr(alerts, "fetch", lambda *a, **k: (_ for _ in ()).throw(alerts.NotRunning("Prometheus does not answer")))
    data = json.loads(invoke(monkeypatch, finished, "last", "--json").stdout)
    assert data["at_export"]["nodes"] is None and data["at_export"]["faults"] is None and data["at_export"]["alerts"] is None
    md = invoke(monkeypatch, finished).stdout
    assert "not read (Docker does not answer)" in md and "Faults now: not read" in md and "not read (Prometheus is not running)" in md


# ---- what the report must never hold ----
HOME = "/" + "home" + "/someone"  # built, so this file holds no home path for the privacy scan
SENTINELS = ["SECRETVALUE", HOME, "/var/lib/private", "Bearer abc", "10.141.0.5", "node7.corp.example.com", "hunter2hunter2", "someone"]


def test_no_secret_body_hostname_or_path_reaches_either_output(monkeypatch, finished, cluster_now):
    finished["steps"][0].update(status="failed", detail="POST http://10.141.0.5:8081/sim failed: Authorization: Bearer abcdefghijkl; password=hunter2hunter2 at " + HOME + "/.cache/x.log",
                                do={"nodes": ["cn001"], "temp": 90, "token": "SECRETVALUE"})
    finished["steps"][1].update(target="nothing-known", do={"api_key": "SECRETVALUE", "body": "SECRETVALUE"}, status="failed",
                                detail="node7.corp.example.com refused; see /var/lib/private/state/db and ~/.ssh/key")
    finished["expects"][0]["detail"] = "got token=hunter2hunter2 from 10.141.0.5:8081"
    finished["ok"] = False
    for args in ((), ("last", "--json")):
        r = invoke(monkeypatch, finished, *args)
        assert r.exit_code == 0, r.output
        for s in SENTINELS:
            assert s not in r.stdout, f"{s!r} leaked with {args}"
        assert "<redacted>" in r.stdout and "<path>" in r.stdout and "<address>" in r.stdout and "<host>" in r.stdout
    out = invoke(monkeypatch, finished).stdout
    assert "A step on nothing-known" in out and '{"' not in out.split("## Expectations")[0]  # a step with no words never prints its raw action


def test_a_scenario_ticket_summary_is_kept_but_never_a_do_dump(finished):
    data, _md = testreport.build({**finished, "steps": [{"at": 0, "target": "jira", "do": {"tickets": {"action": "create", "count": 2, "summary": "Wave"}}, "page": True, "status": "ok", "t": 0}]}, None, None)
    assert data["steps"][0]["action"] == "Create 2 Jira tickets: Wave" and "tickets" not in json.dumps(data["steps"][0]).replace("Jira tickets", "")


def test_scrub_keeps_plain_words_and_the_mounts_a_scenario_names():
    assert testreport.scrub("slurmctld refused the job: no such partition") == "slurmctld refused the job: no such partition"
    assert testreport.scrub("/scratch is 99% full on cn001") == "/scratch is 99% full on cn001"
    assert testreport.scrub("") == "" and testreport.scrub(None) == ""
    assert testreport.scrub("cat /etc/slurm/slurm.conf failed") == "cat <path> failed"
