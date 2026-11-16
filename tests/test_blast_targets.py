"""A declared blast entry for every scenario target kind (ADR 0086 phase 2, issue 809).

Before Play the page marks, for each step, the parts whose clients may see a change. That comes from `webui/blast.py` TARGETS, one
reviewed entry per target kind, joined with what the step implies (`scenario.side_effects`). A target kind the engine accepts with no
entry fails here, and so does a part id the Circuit view does not draw.
"""
import re
from pathlib import Path

import yaml

from benchctl import scenario
from webui import blast

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
WITH_TOPOLOGY = {**SIM, "topology": {"plugin": "tree", "spines": 1, "leaves_per_spine": 2}}


def engine_targets() -> set[str]:
    """Every `target:` a scenario step may name once `plan()` has expanded fault models: the mocks' endpoints, the host targets, a switch."""
    return (set(scenario.ENDPOINTS) - {"jira-tickets", "jira-activity"}) | set(scenario.HOST_TARGETS) | {"switch"}


def test_every_target_kind_has_a_declared_entry_with_words():
    assert engine_targets() <= set(blast.TARGETS), f"no blast entry for: {sorted(engine_targets() - set(blast.TARGETS))}"
    for name, entry in blast.TARGETS.items():
        assert entry["caption"].endswith(".") and len(entry["caption"]) > 30, name
    assert set(blast.AUTH_PARTS) == set(__import__("benchctl.authfaults", fromlist=["KEYS"]).KEYS)  # every auth fault names its parts


def test_every_part_is_one_the_circuit_view_draws():
    drawn = set(re.findall(r"'([a-z0-9-]+)'", (ROOT / "webui/static/js/circuitlayout.js").read_text()))
    named = {p for e in blast.TARGETS.values() for p in e["parts"]} | {p for ps in blast.AUTH_PARTS.values() for p in ps}
    assert named <= drawn, f"not drawn by the Circuit view: {sorted(named - drawn)}"


def test_every_shipped_step_says_whose_clients_may_see_a_change():
    for sc in scenario.list_all(ROOT / "scenarios", sim=WITH_TOPOLOGY):
        pv = blast.preview(sc, WITH_TOPOLOGY)
        for row in pv["steps"]:
            assert row["blast"] and row["blast_caption"], (sc["name"], row["n"], row["target"])


def test_a_power_change_reaches_what_the_simulator_says_it_does():
    off = blast.step_blast(scenario.Step(0, "bmc-farm", {"nodes": ["cn002"], "power": "Off"}))
    assert off["blast"] == ["bmc-farm", "bcm-mock", "gpu-exporter", "dcgm-exporter", "slurmrestd", "login", "jira-mock"]
    assert "Redfish" in off["blast_caption"]
    assert blast.step_blast(scenario.Step(0, "auth", {"teleport_cert_ttl": {"user": "alice", "ttl": "60s"}}))["blast"] == ["teleport"]
    assert blast.step_blast(scenario.Step(0, "bmc-farm", {"nodes": ["cn002"], "temp": 70}))["blast"] == ["bmc-farm"]  # under 85 C: no ticket
