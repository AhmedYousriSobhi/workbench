"""Slurm topology from sim.yaml (issue 543): topology.conf, the plugin line, the switch scenario step."""
import json
from pathlib import Path

import pytest
import yaml
from invhelp import inv

from benchctl import compose, k8s, scenario, slurmconf, topology

SIM = yaml.safe_load(Path("sim.yaml").read_text())
TREE = {**SIM, "topology": {"plugin": "tree", "spines": 1, "leaves_per_spine": 2}}


class CP:
    def __init__(self, code=0):
        self.stdout, self.stderr, self.returncode = "", "", code


def test_without_a_topology_key_nothing_changes(tmp_path):
    assert topology.conf(SIM) == "" and topology.leaves(SIM) == {} and topology.plugin_line(SIM) is None
    assert "TopologyPlugin" not in slurmconf.slurm_conf(SIM)
    (tmp_path / "topology.conf").write_text("stale")
    slurmconf.write(SIM, tmp_path)
    assert not (tmp_path / "topology.conf").exists()  # an earlier run's switches do not outlive the key


def test_tree_has_one_switch_per_leaf_spines_above_and_nodes_spread_round_robin():
    assert topology.conf(TREE).splitlines()[1:] == [
        "SwitchName=leaf1 Nodes=cn[001,003],gpu001", "SwitchName=leaf2 Nodes=cn[002,004],gpu002", "SwitchName=spine1 Switches=leaf[1-2]"]
    two = {**SIM, "compute": {**SIM["compute"], "count": 6}, "topology": {"spines": 2, "leaves_per_spine": 2}}
    lines = topology.conf(two).splitlines()
    assert "SwitchName=spine2 Switches=leaf[3-4]" in lines and lines[-1] == "SwitchName=root Switches=spine[1-2]"
    assert "SwitchName=leaf1 Nodes=cn[001,005]" in lines


def test_every_slurm_node_is_under_exactly_one_leaf_and_agrees_with_the_inventory():
    nine = {**SIM, "compute": {**SIM["compute"], "count": 9}, "topology": {"spines": 2, "leaves_per_spine": 3}}
    under = [n for nodes in topology.leaves(nine).values() for n in nodes]
    names = {n["name"] for n in json.loads(inv(nine).read_text())["nodes"]}  # the one inventory (issue 462)
    assert sorted(under) == sorted(names) and len(under) == len(set(under))
    assert all(topology.leaves(nine).values())  # no empty leaf


def test_the_block_plugin_makes_one_block_per_leaf_with_a_size_every_block_fills():
    block = {**SIM, "topology": {"plugin": "block", "spines": 1, "leaves_per_spine": 2}}
    assert topology.conf(block).splitlines()[1:] == ["BlockName=leaf1 Nodes=cn[001,003],gpu001", "BlockName=leaf2 Nodes=cn[002,004],gpu002", "BlockSizes=2"]
    assert topology.block_size({**block, "topology": {**block["topology"], "block_size": 1}}) == 1
    assert topology.plugin_line(block) == "TopologyPlugin=topology/block" and "TopologyPlugin=topology/block" in slurmconf.slurm_conf(block)


def test_the_plugin_line_and_file_are_generated(tmp_path):
    assert "TopologyPlugin=topology/tree" in slurmconf.slurm_conf(TREE)
    files = slurmconf.write(TREE, tmp_path)
    assert tmp_path / "topology.conf" in files and (tmp_path / "topology.conf").read_text() == topology.conf(TREE)


def test_bad_settings_are_named_once_each():
    def probs(**t):
        return topology.problems({**SIM, "topology": t})
    assert "plugin must be" in probs(plugin="ring")[0]
    assert "spines must be" in probs(spines=0)[0] and "leaves_per_spine must be" in probs(leaves_per_spine=99)[0]
    assert "only 6 Slurm nodes" in probs(spines=4, leaves_per_spine=4)[0]
    assert "block_size must be" in probs(plugin="block", block_size=9)[0]
    with pytest.raises(topology.TopologyError, match="leaves"):
        compose.generate({**SIM, "topology": {"spines": 4, "leaves_per_spine": 4}}, {"groups": ["core"]})


def test_the_profile_node_count_decides_the_leaves():
    assert len(topology.slurm_nodes(TREE, 16)) == 18 and topology.leaves(TREE, 16)["leaf1"][:2] == ["cn001", "cn003"]
    assert topology.problems({**SIM, "topology": {"spines": 4, "leaves_per_spine": 4}}, 16) == []  # 18 nodes fill 16 leaves


def test_hostlist_joins_runs_and_prefixes():
    assert topology.hostlist(["cn001", "cn002", "cn003", "cn005", "gpu001"]) == "cn[001-003,005],gpu001"
    assert topology.hostlist(["cn001"]) == "cn001" and topology.hostlist(["cn009", "cn010"]) == "cn[009-010]"


@pytest.fixture
def lab(monkeypatch):
    monkeypatch.setattr(k8s, "current_sim", lambda: TREE)


def test_a_leaf_down_drains_exactly_its_nodes_and_up_resumes_only_those(lab):
    calls = []

    def run(cmd):
        calls.append(cmd)
        return CP()
    down = scenario.execute(scenario.Step(0, "switch", {"leaf": "leaf1", "state": "down"}), runner=run)
    assert down.status == "ok" and [c[5] for c in calls] == ["nodename=cn001", "nodename=cn003", "nodename=gpu001"]
    assert all("state=drain" in c and "reason=switch leaf1 down" in c for c in calls)
    calls.clear()
    up = scenario.execute(scenario.Step(0, "switch", {"leaf": "leaf1", "state": "up"}), runner=run)
    assert up.status == "ok" and [c[5] for c in calls] == ["nodename=cn001", "nodename=cn003", "nodename=gpu001"]
    assert all(c[-1] == "state=resume" for c in calls)


def test_the_page_plays_a_switch_step_through_the_governor(lab):
    seen = []
    step = scenario.Step(0, "switch", {"leaf": "leaf2", "state": "down"})
    assert scenario.page_support(step) == ""
    out = scenario.execute(step, host=False, slurm=lambda n, s, r: (seen.append((n, s, r)), (True, ""))[1])
    assert out.status == "ok" and seen == [("cn002", "drain", "switch leaf2 down"), ("cn004", "drain", "switch leaf2 down"), ("gpu002", "drain", "switch leaf2 down")]


def test_validate_names_a_missing_topology_leaf_and_state():
    sc = {"steps": [{"at": "0s", "target": "switch", "do": {"leaf": "leaf9", "state": "sideways"}}]}
    assert scenario.validate(sc, TREE) and "leaf1" in scenario.validate(sc, TREE)[0]
    assert "need a valid `topology:`" in scenario.validate(sc, SIM)[0]
    assert scenario.validate(scenario.load("switch-leaf-down"), TREE) == []
