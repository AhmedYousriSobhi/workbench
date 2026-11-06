"""The declared graph model of the Infrastructure architecture (#312): its edges, its measured flags, and the page that draws it."""
import re
from pathlib import Path

from webui import flow, graph

ROOT = Path(__file__).resolve().parent.parent
NODES = [{"name": "cn001", "kind": "compute"}, {"name": "gn001", "kind": "gpu"}]
SERVICES = {"bmc-farm": True, "bcm-mock": True, "jira-mock": True, "gpu-exporter": True}


def logins(*modes):
    return [{"name": "login" if i == 0 else f"login{i + 1}", "mode": m} for i, m in enumerate(modes)]


def ids(g):
    return {e["id"] for e in g["edges"]}


def test_edge_ids_are_unique_and_every_end_is_an_entity():
    g = graph.build({**SERVICES, "teleport": True, "jira-real": True}, NODES, logins("ssh", "teleport", "both"))
    assert len(ids(g)) == len(g["edges"])
    known = {e["id"] for e in g["entities"]}
    assert all(e["from"] in known and e["to"] in known for e in g["edges"])
    assert all(e["plane"] in graph.PLANES for e in g["edges"])


def test_without_teleport_there_is_no_teleport_entity_or_edge():
    g = graph.build(SERVICES, NODES, logins("ssh"))
    assert "teleport" not in {e["id"] for e in g["entities"]}
    assert not [e for e in g["edges"] if "teleport" in (e["from"], e["to"], e["kind"])]
    assert ids(g) >= {"clients>login:ssh", "login>nodes:jump", "clients>bmc-farm:api"}


def test_login_mode_decides_which_access_edges_exist():
    g = graph.build({**SERVICES, "teleport": True}, NODES, logins("ssh", "teleport", "both"))
    got = ids(g)
    assert "clients>login:ssh" in got and "clients>login2:ssh" not in got and "clients>login3:ssh" in got  # direct SSH: ssh and both
    assert "teleport>login:teleport" not in got and "teleport>login2:teleport" in got and "teleport>login3:teleport" in got
    assert {"login>nodes:jump", "login2>nodes:jump", "login3>nodes:jump", "teleport>nodes:teleport"} <= got


def test_measured_only_where_a_counter_backs_the_edge():
    g = graph.build({**SERVICES, "teleport": True, "jira-real": True}, NODES, logins("both"))
    by = {e["id"]: e for e in g["edges"]}
    for svc in (*flow.STATS, *flow.COUNTERS):  # every service with a request counter, Teleport's connection counter included
        assert by[f"clients>{svc}:api"]["measured"], svc
    assert not by["clients>jira-real:api"]["measured"]  # the real Jira publishes no counter: declared only
    assert not any(e["measured"] for e in g["edges"] if e["kind"] in ("ssh", "jump", "teleport"))  # nobody counts SSH hops


def test_fault_edges_exist_only_for_what_a_scenario_can_reach():
    g = graph.build({**SERVICES, "jira-real": True}, NODES, logins("ssh"))
    assert {e["to"] for e in g["edges"] if e["kind"] == "inj"} == set(flow.SCENARIO_TARGETS.values())


def test_planes_follow_what_a_client_uses_the_service_for():
    g = graph.build({**SERVICES, "jira-real": True}, NODES, logins("ssh"))
    plane = {e["to"]: e["plane"] for e in g["edges"] if e["id"].endswith(":api")}
    assert plane == {"bmc-farm": "management", "bcm-mock": "management", "gpu-exporter": "telemetry",
                     "jira-mock": "tickets", "jira-real": "tickets"}


def test_nodes_are_entities_in_the_nodes_group():
    g = graph.build(SERVICES, NODES, logins("ssh"))
    assert [(e["id"], e["type"], e["group"]) for e in g["entities"] if e.get("group")] == [("cn001", "compute-node", "nodes"), ("gn001", "gpu-node", "nodes")]


def test_page_draws_only_edges_named_in_the_model():
    """Every edge id the page builds (in both arrangements, narrow and column) is one webui/graph.py can produce."""
    page = (ROOT / "webui" / "static" / "index.html").read_text()
    used = {x for x in re.findall(r"[`']([^`']*>[^`']*:(?:api|ssh|teleport|jump|inj))[`']", page)}
    model = graph.build({**SERVICES, "teleport": True, "jira-real": True}, NODES, logins("both", "teleport"))
    shapes = {re.sub(r"\bcn\d+|\bgn\d+", "N", e["id"]) for e in model["edges"]}
    pattern = {  # the page's template ids, turned into what the model would name them
        "${l.name}>nodes:jump": {"login>nodes:jump", "login2>nodes:jump"},
        "clients>${k}:api": {f"clients>{s}:api" for s in (*SERVICES, "jira-real")},
        "clients>${l.name}:ssh": {"clients>login:ssh", "clients>login2:ssh"},
        "clients>teleport:api": {"clients>teleport:api"},
        "faults>${k}:inj": {f"faults>{s}:inj" for s in flow.SCENARIO_TARGETS.values()},
        "teleport>${l.name}:teleport": {"teleport>login:teleport", "teleport>login2:teleport"},
        "teleport>nodes:teleport": {"teleport>nodes:teleport"},
    }
    assert used == set(pattern), used ^ set(pattern)  # a new edge in the page needs a line here and in graph.py
    for name, ids_ in pattern.items():
        assert ids_ & shapes, name  # at least one concrete id of this shape exists in the model
    assert "data-edge" in page
