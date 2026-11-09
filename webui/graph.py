"""The Infrastructure architecture as data (issue #312): entities and typed edges, derived from the cluster snapshot.

The page used to know its edges only through the code that drew them. Anything that asks "what is connected to this,
by what, and is that measured?" (focus view, planes, blast radius, the client's-eye lens) needs them as data, so the
drawing reads this model and a test can compare the two.

An edge is `measured` only when something real counts it (a mock's request counter, Teleport's connection counter, the
fault events the page itself records). Every other edge is declared: it says how the parts are wired, not that traffic
was seen. The page must never present a declared edge as measured traffic.
"""
from . import comms
from .flow import SCENARIO_TARGETS

PLANES = ("access", "management", "telemetry", "tickets", "faults")
# service -> the plane its API belongs to (what a client uses it for)
SERVICE_PLANE = {"bmc-farm": "management", "bcm-mock": "management", "gpu-exporter": "telemetry",
                 "jira-mock": "tickets", "jira-real": "tickets", "k8s-api": "management", "dcgm-exporter": "telemetry", "real-gpu": "telemetry"}
MEASURED = comms.MEASURED  # the real Jira publishes no counter, so it is declared only
INJECTABLE = set(SCENARIO_TARGETS.values())  # blocks a scenario step or fault button can reach (flow.note)


def _edge(src: str, dst: str, plane: str, kind: str, measured: bool) -> dict:
    return {"id": f"{src}>{dst}:{kind}", "from": src, "to": dst, "plane": plane, "kind": kind, "measured": measured}


def build(services: dict, nodes: list, logins: list, cluster: str | None = None) -> dict:
    """{"entities": [...], "edges": [...]} for the services, nodes and login nodes of one snapshot.

    `kind` is how the line is drawn (api, ssh, teleport, jump, inj); `plane` is what it is for. With `cluster` every
    entity and edge carries it and its id (and edge ends) is prefixed `cluster/`, so graphs of several clusters can sit in
    one list without clashing (#328).
    """
    teleport = "teleport" in services
    jump, ctl = "jump" in services, "controller" in services
    svcs = [s for s in services if s not in ("teleport", "jump", "controller")]
    ents = [{"id": "clients", "type": "clients"}, {"id": "faults", "type": "injector"}]
    ents += [{"id": lg["name"], "type": "login", "mode": lg["mode"], "kind": lg.get("kind", "both")} for lg in logins]
    ents += [{"id": "teleport", "type": "access"}] if teleport else []
    ents += [{"id": "jump", "type": "access"}] if jump else []
    ents += [{"id": "controller", "type": "controller"}] if ctl else []
    ents += [{"id": s, "type": "service"} for s in svcs]
    slurm_nodes = [n for n in nodes if n["kind"] != "k8s"]
    k8s_nodes = [n for n in nodes if n["kind"] == "k8s"]
    ents += [{"id": "nodes", "type": "group"}] + [{"id": n["name"], "type": n["kind"] + "-node", "group": "nodes"} for n in slurm_nodes]
    ents += ([{"id": "k8s-nodes", "type": "group"}] + [{"id": n["name"], "type": "k8s-node", "group": "k8s-nodes"} for n in k8s_nodes]) if k8s_nodes else []
    edges = []
    if teleport:
        edges.append(_edge("clients", "teleport", "access", "api", "teleport" in MEASURED))
    for lg in logins:
        if lg["mode"] != "teleport":
            edges.append(_edge("clients", lg["name"], "access", "ssh", False))
        if lg["mode"] != "ssh" and teleport:
            edges.append(_edge("teleport", lg["name"], "access", "teleport", False))
    if jump:  # the bastion fronts every key-login node and the controller; Teleport nodes keep Teleport
        edges.append(_edge("clients", "jump", "access", "ssh", False))
        edges += [_edge("jump", lg["name"], "access", "jump", False) for lg in logins if lg["mode"] != "teleport"]
        edges += [_edge("jump", "controller", "access", "jump", False)] if ctl else []
    if ctl:
        edges.append(_edge("clients", "controller", "access", "ssh", False))
        edges += [_edge(lg["name"], "controller", "access", "jump", False) for lg in logins if lg["mode"] != "teleport"]
    for s in svcs:
        edges.append(_edge("clients", s, SERVICE_PLANE.get(s, "management"), "api", s in MEASURED))
        if s in INJECTABLE:
            edges.append(_edge("faults", s, "faults", "inj", True))
    for lg in logins:  # an s-login jumps to the Slurm nodes, a k-login reaches the Kubernetes nodes with kubectl
        kind = lg.get("kind", "both")
        if kind in ("slurm", "both"):
            edges.append(_edge(lg["name"], "nodes", "access", "jump", False))
        if kind in ("k8s", "both") and k8s_nodes:
            edges.append(_edge(lg["name"], "k8s-nodes", "access", "kubectl", False))
    if teleport:
        edges.append(_edge("teleport", "nodes", "access", "teleport", False))
    if cluster:
        pre = f"{cluster}/"
        ents = [{**e, "id": pre + e["id"], "cluster": cluster, **({"group": pre + e["group"]} if "group" in e else {})} for e in ents]
        edges = [{**e, "id": pre + e["id"], "from": pre + e["from"], "to": pre + e["to"], "cluster": cluster} for e in edges]
    return {"entities": ents, "edges": edges}


def attach(snap: dict) -> dict:
    """The snapshot with its graph(s). One cluster (today's snapshot) gets `graph`; a snapshot with `clusters` (each: name,
    nodes, logins, services, optionally reachable and faults) gets, per cluster, its own unprefixed `graph` (what the page
    shows when that cluster is entered) and a top-level `graph` of all of them, ids prefixed with the cluster name. Clusters
    are independent: no edge ever joins two of them."""
    clusters = snap.get("clusters")
    if not clusters:
        return {**snap, "graph": build(snap["services"], snap["nodes"], snap["logins"]), "comms": comms.build(snap["services"], snap["nodes"], snap["logins"]), "absent": comms.absent(snap["services"], snap["nodes"], snap["logins"])}
    per = [{**c, "graph": build(c["services"], c["nodes"], c["logins"]), "comms": comms.build(c["services"], c["nodes"], c["logins"]), "absent": comms.absent(c["services"], c["nodes"], c["logins"])} for c in clusters]
    merged = [build(c["services"], c["nodes"], c["logins"], c["name"]) for c in clusters]
    return {**snap, "clusters": per, "graph": {"entities": [e for g in merged for e in g["entities"]], "edges": [e for g in merged for e in g["edges"]]}}
