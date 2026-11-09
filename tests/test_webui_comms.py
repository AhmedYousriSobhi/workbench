from pathlib import Path

from webui import comms, graph

SVC = {"bmc-farm": True, "gpu-exporter": True, "jira-real": True}
NODES = [{"name": "cn001", "kind": "cpu"}]
LOGINS = [{"name": "login", "mode": "ssh"}]


def by(links):
    return {(link["from"], link["to"]): link for link in links}


def test_links_follow_the_parts_the_snapshot_has():
    got = by(comms.build(SVC, NODES, LOGINS))
    assert (
        got[("slurmctld", "slurmd")]["port"] == 6818 and got[("login", "slurmctld")]["port"] == 6817
    )
    assert (
        ("clients", "bcm-mock") not in got
        and ("clients", "k8s-api") not in got
        and ("etcd" not in {k[1] for k in got})
    )


def test_measured_only_where_a_counter_backs_it_and_real_vs_simulated_stay_apart():
    got = by(comms.build(SVC, NODES, LOGINS))
    assert got[("clients", "bmc-farm")]["measured"] and not got[("clients", "bmc-farm")]["real"]
    assert not got[("clients", "jira-real")]["measured"] and got[("clients", "jira-real")]["real"]
    assert not got[("slurmctld", "slurmd")]["measured"]


def test_kubernetes_links_need_the_api_and_no_slurm_without_slurm_parts():
    got = by(comms.build({"k8s-api": True}, [{"name": "kcn001", "kind": "k8s"}], []))
    assert got[("k8s-api", "etcd")]["port"] == 2379 and not got[("kwok", "k8s-api")]["real"]
    assert ("slurmctld", "slurmd") not in got


def test_attach_adds_comms_per_cluster():
    snap = {"services": SVC, "nodes": NODES, "logins": LOGINS}
    assert graph.attach(snap)["comms"] == comms.build(SVC, NODES, LOGINS)
    two = graph.attach({**snap, "clusters": [{"name": "a", **snap}, {"name": "b", **snap}]})
    assert all(c["comms"] for c in two["clusters"])


def test_the_api_server_link_is_counted_but_the_architecture_edge_is_not():
    from webui import graph
    link = {(x["from"], x["to"]): x for x in comms.build({"k8s-api": True}, [{"name": "kcn001", "kind": "k8s"}], [])}[("clients", "k8s-api")]
    assert link["measured"] is True and link["service"] == "k8s-api"
    assert "k8s-api" not in graph.MEASURED  # its total counts every caller, so the drawing never shows it as client traffic


def test_absent_lists_the_links_a_cluster_lacks_and_the_unreadable_ones():
    got = {r["id"]: r for r in comms.absent({"k8s-api": True}, [{"name": "kcn001", "kind": "k8s"}], [])}
    assert got["login>slurmctld:6817"]["why"] == "not in this cluster"
    assert got["slurmctld>slurmdbd:6819"]["why"] == "the page cannot read this yet"
    assert got["nodes>storage:None"]["why"] == "planned, not built yet"
    assert "kube-scheduler>k8s-api:6443" not in got  # present, so not absent


def test_every_link_endpoint_has_a_place_in_the_fixed_drawing():
    page = (Path(__file__).parent.parent / "webui/static/index.html").read_text()
    ends = {e for k in [*comms.LINKS, *comms.UNREADABLE, *comms.PLANNED] for e in k[:2]}
    assert [e for e in sorted(ends) if f"'{e}'" not in page] == []


def test_every_part_of_the_drawing_has_a_description_for_the_click_view():
    page = (Path(__file__).parent.parent / "webui/static/index.html").read_text()
    block = page[page.index("const PART_INFO = {") : page.index("const PART_ICON")]
    ends = {e for k in [*comms.LINKS, *comms.UNREADABLE, *comms.PLANNED] for e in k[:2]}
    assert [e for e in sorted(ends) if f"'{e}':" not in block] == []


def test_the_real_dcgm_exporter_link_exists_only_when_built():
    on = {(r["from"], r["to"]): r for r in comms.build({"dcgm-exporter": True}, [], [])}
    assert on[("clients", "dcgm-exporter")]["port"] == 9402 and on[("clients", "dcgm-exporter")]["real"] and not on[("clients", "dcgm-exporter")]["measured"]
    assert ("clients", "dcgm-exporter") not in {(r["from"], r["to"]) for r in comms.build({"gpu-exporter": True}, [], [])}


def test_the_real_gpu_link_exists_only_when_built():
    on = {(r["from"], r["to"]): r for r in comms.build({"real-gpu": True}, [], [])}
    assert on[("clients", "real-gpu")]["port"] == 9403 and on[("clients", "real-gpu")]["real"] and not on[("clients", "real-gpu")]["measured"]
    assert ("clients", "real-gpu") not in {(r["from"], r["to"]) for r in comms.build({"dcgm-exporter": True}, [], [])}
