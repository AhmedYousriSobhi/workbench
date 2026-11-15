"""Where each scenario step lands on the Infrastructure architecture (ADR 0086, issue 808).

`scenario.touches(step)` turns a planned step into the diagram ids it acts on. A step that lands nowhere must say why, and must be
listed in UNMAPPED below. That list may only shrink: a new unmapped step fails, and so does an entry that now maps to a part.
"""
import re
from pathlib import Path

import yaml

from benchctl import k8s, scenario
from benchctl.nodes import node_names

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
WITH_TOPOLOGY = {**SIM, "topology": {"plugin": "tree", "spines": 1, "leaves_per_spine": 2}}  # the switch scenario needs one (issue 543)

# (scenario, YAML step number) -> why it has no part on the diagram. Only shrinks: map a step to a part, then remove its entry.
UNMAPPED = {
    ("netem-slow-link", 2): "it clears the network delay on every node at once, not on one part",
}


def planned():
    for sc in scenario.list_all(ROOT / "scenarios", sim=WITH_TOPOLOGY):
        for step in scenario.plan(sc):
            yield sc["name"], step


def test_every_step_lands_on_a_part_or_is_listed_with_its_reason():
    unmapped = {}
    for name, step in planned():
        hit = scenario.touches(step, WITH_TOPOLOGY)
        assert step.n >= 1, (name, step)
        if not hit["ids"]:
            assert hit["why"], f"{name} step {step.n}: no part and no reason"
            unmapped[(name, step.n)] = hit["why"]
    new = {k: v for k, v in unmapped.items() if k not in UNMAPPED}
    gone = sorted(k for k in UNMAPPED if k not in unmapped)
    assert not new, f"steps with no part on the diagram (map them in scenario.touches, or list them in UNMAPPED with the reason): {new}"
    assert not gone, f"these steps now land on a part: remove them from UNMAPPED: {gone}"
    assert all(unmapped[k] == why for k, why in UNMAPPED.items()), "the stated reason changed: update UNMAPPED"


def test_every_id_is_a_node_or_a_part_the_circuit_view_draws():
    layout = (ROOT / "webui/static/js/circuitlayout.js").read_text()
    drawn = set(re.findall(r"'([a-z0-9-]+)'", layout))  # part and service ids are quoted in circuitSpec
    nodes = {n for n, _ in node_names(WITH_TOPOLOGY)} | {n for n, _ in k8s.node_list(WITH_TOPOLOGY)}
    for name, step in planned():
        for i in scenario.touches(step, WITH_TOPOLOGY)["ids"]:
            assert i in nodes or i in drawn, f"{name} step {step.n}: {i!r} is neither a node nor a part of the Circuit view"


def test_expansions_keep_the_number_of_the_step_that_made_them():
    ramp = scenario.plan({"steps": [{"at": "0s", "target": "slurm", "do": {"drain": ["cn001"]}},
                                    {"at": "10s", "target": "bmc-farm", "do": {"nodes": ["gpu001"], "ramp": {"to": 90, "over": "30s"}}}]})
    assert [s.n for s in ramp] == [1, 2, 2, 2]
    fault = scenario.plan({"steps": [{"at": "0s", "target": "fault", "do": {"inject": "fan-failure", "nodes": ["gpu002"]}}]})
    assert fault and {s.n for s in fault} == {1}


def test_node_down_lands_on_the_services_slurmctld_and_the_node():
    sc = scenario.load("node-down", ROOT / "scenarios", SIM)
    p = scenario.preview(sc, SIM)
    assert [(s["n"], s["ids"]) for s in p["steps"]] == [
        (1, ["bmc-farm", "cn002"]), (2, ["bcm-mock", "cn002"]), (3, ["slurmctld", "cn002"]),
        (4, ["bmc-farm", "cn002"]), (5, ["bcm-mock", "cn002"]), (6, ["slurmctld", "cn002"])]
    assert all(s["no_part"] == "" for s in p["steps"])
    assert p["expects"] == [{"label": 'slurm.node_state {"node": "cn002", "state": "drain"}', "page": False, "e": 1, "ids": ["cn002"]}]


def test_each_target_kind_names_its_part():
    T = lambda target, do: scenario.touches(scenario.Step(0, target, do), WITH_TOPOLOGY)["ids"]
    assert T("jira", {"tickets": {"action": "create", "summary": "x"}}) == ["jira-mock"]
    assert T("jira-real", {"fault": "outage"}) == ["jira-real"]
    assert T("slurm", {"submit": {"node": "gpu001", "gpus": 2}}) == ["slurmctld", "gpu001"]
    assert T("slurm", {"cancel": True}) == ["slurmctld"]
    assert T("ssh", {"state": "down"}) == ["login"]
    assert T("ssh", {"node_sshd": "down", "nodes": ["cn003"]}) == ["cn003"]
    assert T("auth", {"slurm_token_ttl": {"user": "alice", "ttl": "60s"}}) == ["slurmrestd"]
    assert T("auth", {"munge_wrong_key": ["cn001"]}) == ["slurmctld", "cn001"]
    assert T("auth", {"teleport_cert_restore": ["alice"]}) == ["teleport"]
    assert T("k8s", {"drain": {"node": "k8s-cpu001"}}) == ["k8s-api", "k8s-cpu001"]
    assert T("k8s", {"apply": ["x.yaml"]}) == ["k8s-api"]
    assert T("slurm-upgrade", {"to": "26.05", "daemons": ["slurmdbd", "cn001"]}) == ["slurmdbd", "cn001"]
    assert T("dcgm", {"nodes": ["gpu001"], "stop": True}) == ["dcgm-exporter", "gpu001"]
    assert T("netem", {"nodes": ["cn001"], "delay": "200ms"}) == ["cn001"]
    assert scenario.touches(scenario.Step(0, "nowhere", {}))["why"]


def test_a_switch_step_lands_on_the_nodes_under_its_leaf():
    ids = scenario.touches(scenario.Step(0, "switch", {"leaf": "leaf1", "state": "down"}), WITH_TOPOLOGY)["ids"]
    assert ids[0] == "slurmctld" and len(ids) > 1
    assert scenario.touches(scenario.Step(0, "switch", {"leaf": "leaf1", "state": "down"}))["ids"] == ["slurmctld"]  # no cluster given: the controller only


def test_expectations_name_the_part_they_assert_on():
    E = scenario.expect_touches
    assert E({"slurm.node_state": {"node": "cn002", "state": "drain"}}) == ["cn002"]
    assert E({"jira.issue_count": {"jql": "x", "count": 1}}) == ["jira-mock"]
    assert E({"jira.issue_status": {"jql": "x", "status": "Done", "target": "real"}}) == ["jira-real"]
    assert E({"k8s.pod_status": {"namespace": "a", "name": "b", "reason": "x"}}) == ["k8s-api"]
    assert E({"storage.mount": {"mount": "/home"}}) == ["gpu-exporter", "cn001"]
    assert E({"unknown.kind": {}}) == []
