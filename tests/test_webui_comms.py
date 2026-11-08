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
