"""Teleport on the page (#187): a service and a connections row only when the part is built, never a phantom one."""
from pathlib import Path

import yaml

from webui import cluster, connections

SIM = yaml.safe_load(Path("sim.yaml").read_text())


def spec_file(tmp_path, parts):
    f = tmp_path / "compose" / "generated" / "workbench.yaml"
    f.parent.mkdir(parents=True)
    f.write_text(yaml.safe_dump({"name": "w", "cpu_nodes": 1, "gpu_nodes": 0, "gpus_per_node": 8, "parts": parts,
                                 "k8s_nodes": 0, "bmc_nodes": None}))


def test_built_follows_the_saved_workbench(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cluster.saved_has_teleport() is False        # nothing saved: a preset, no Teleport
    spec_file(tmp_path, ["jira"])
    assert cluster.saved_has_teleport() is False
    (tmp_path / "compose/generated/workbench.yaml").write_text(yaml.safe_dump({"name": "w", "cpu_nodes": 1, "gpu_nodes": 0,
                                                                              "gpus_per_node": 8, "parts": ["jira", "teleport"],
                                                                              "k8s_nodes": 0, "bmc_nodes": None}))
    assert cluster.saved_has_teleport() is True
    (tmp_path / "compose/generated/workbench.yaml").write_text("not: [valid")
    assert cluster.saved_has_teleport() is False        # a broken file hides Teleport instead of breaking the page


def test_snapshot_lists_teleport_only_when_built(monkeypatch):
    seen = []

    def fetch(url, *a):
        seen.append(url)
        raise OSError("down")
    c = cluster.Cluster(SIM, fetch=fetch)
    assert "teleport" not in c.snapshot()["services"] and not any("3434" in u for u in seen)
    monkeypatch.setattr(cluster, "teleport_built", lambda: True)
    assert c.snapshot()["services"]["teleport"] is False and f"{cluster.TELEPORT}/healthz" in seen


def test_connections_row_names_the_proxy_identity_and_invite():
    row = next(e for e in connections.entities(SIM) if e["id"] == "teleport")
    assert row["address"] == "https://10.141.255.30:3080" and "tsh --proxy=10.141.255.30:3080 --insecure -i" in row["command"] and "teleport.workbench.test" in row["how"]
    assert row["credential"].endswith("teleport/identity/alice") and "benchctl add invite" in row["how"]


def test_the_diagram_is_called_infrastructure_architecture_and_can_draw_teleport():
    html = Path("webui/static/index.html").read_text()
    assert '<h2 id="traffic-h" style="margin:0">Infrastructure architecture</h2>' in html and ">Traffic<" not in html
    assert "const TELEPORT_BOX = ['teleport'" in html and "'teleport': 'teleport'" in html  # a box, and a click to its Connections row
    assert "hasTeleport() ? [...BASE_SERVICES, TELEPORT_BOX]" in html                       # only when the part is built


def test_snapshot_carries_the_login_nodes_of_the_saved_workbench(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cluster.saved_logins() == [{"name": "login", "mode": "ssh", "port": 2222}]   # nothing saved: the one login that takes a key
    f = tmp_path / "compose" / "generated" / "workbench.yaml"
    f.parent.mkdir(parents=True)
    f.write_text(yaml.safe_dump({"name": "w", "cpu_nodes": 1, "gpu_nodes": 0, "gpus_per_node": 8, "parts": ["jira", "teleport"],
                                 "k8s_nodes": 0, "bmc_nodes": None, "login_nodes": ["both", "teleport", "ssh"]}))
    assert cluster.saved_logins() == [{"name": "login", "mode": "both", "port": 2222}, {"name": "login2", "mode": "teleport", "port": None},
                                      {"name": "login3", "mode": "ssh", "port": 2224}]  # a Teleport-only node publishes no port
    f.write_text("not: [valid")
    assert cluster.saved_logins() == [{"name": "login", "mode": "ssh", "port": 2222}]   # a broken file does not break the page


def test_the_diagram_draws_access_paths_and_every_part_has_a_tip_and_a_pop_up():
    html = Path("webui/static/index.html").read_text()
    for needle in ("'direct SSH'", "'jump host'", "'Teleport'", "MODE_TEXT", "loginList()",     # one labelled line per way in
                   'id="tip"', 'role="tooltip"', 'id="pop"', 'role="dialog"', "function openPop(", "function showTip(",
                   'id="treetoggle"', "function setTree(", "workbench-tree"):                   # the node list folds away
        assert needle in html, needle


def test_the_options_sheet_survives_a_backend_without_logins():
    html = Path("webui/static/index.html").read_text()
    assert "const lg = j.logins;" in html and "need the newer page backend" in html          # parts still listed, the login rows say why they are off
    assert "./scripts/benchctl stop" in html and "different versions" in html               # and the failure names its cure


def test_login_boxes_carry_the_private_address(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "compose" / "generated" / "workbench.yaml"
    f.parent.mkdir(parents=True)
    f.write_text(yaml.safe_dump({"name": "w", "cpu_nodes": 1, "gpu_nodes": 0, "gpus_per_node": 8, "parts": ["jira", "teleport"],
                                 "k8s_nodes": 0, "bmc_nodes": None, "login_nodes": ["ssh", "teleport"]}))
    rows = cluster.saved_logins(SIM)
    assert rows[0]["ip"] == "10.141.255.254" and rows[1]["ip"] == "10.141.255.252" and rows[1]["port"] is None
    assert "ip" not in cluster.saved_logins()[0]
