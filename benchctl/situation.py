"""`describe workbench`: one compact object for an agent that has just arrived (SPEC AG-2, ADR 0061).

It reads what the other read verbs read (the saved selection, the containers, the governor, the Connections registry, the
scenario files, the page's node states) and adds no access path. No field holds a secret: a credential is a file path.
"""
from __future__ import annotations

from collections.abc import Callable

MAX_CHARS = 6700  # for the default profile; tests/test_json_contract.py checks it
SUMMARY = 30


def short(text: str, n: int = SUMMARY) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def auth_kind(entity: dict) -> str:
    if not entity.get("credential"):
        return "none"
    return "ssh-key" if entity.get("kind") == "ssh" else "file"


def endpoints(entities: list[dict]) -> list[dict]:
    return [{"id": e["id"], "address": e["address"], "auth": auth_kind(e), **({"credential": e["credential"]} if e.get("credential") else {})} for e in entities]


def clusters(spec: dict | None, sim: dict, k8s_nodes: list[dict]) -> list[dict]:
    """The Slurm cluster and, when built, the Kubernetes one, each with its pools (node kinds and the Kubernetes pool label)."""
    cpu = spec["cpu_nodes"] if spec else sim.get("compute", {}).get("count", 0)
    gpu = spec["gpu_nodes"] if spec else sim.get("gpu", {}).get("nodes", 0)
    gpn = spec["gpus_per_node"] if spec else sim.get("gpu", {}).get("gpus_per_node", 0)
    out = [{"name": "slurm", "scheduler": "slurm", "pools": [{"name": "compute", "nodes": cpu}, {"name": "gpu", "nodes": gpu, "gpus_per_node": gpn}]}]
    if k8s_nodes:
        pools: dict[str, int] = {}
        for g in k8s_nodes:
            pools[str(g.get("pool", "cpu"))] = pools.get(str(g.get("pool", "cpu")), 0) + int(g.get("count", 1))
        out.append({"name": "kubernetes", "scheduler": "kubernetes", "pools": [{"name": n, "nodes": c} for n, c in pools.items()]})
    return out


def scenario_rows(scs: list[dict], plan: Callable[[dict], list]) -> list[dict]:
    rows = []
    for sc in scs:
        steps = plan(sc)
        rows.append({"name": sc["name"], "summary": short(sc.get("description", "")), "seconds": round(max((s.at for s in steps), default=0))})
    return rows


def gather(*, state: dict, spec: dict | None, sim: dict, k8s_nodes: list[dict], entities: list[dict], faults: list[dict],
           scenarios: list[dict], fault_targets: list[str], plan: Callable[[dict], list]) -> dict:
    running = state["containers"] > 0
    nxt = (["benchctl get faults --json", "benchctl scenario run NAME --json  (NAME from scenarios)", "benchctl wait ready --timeout 120"]
           if running else ["benchctl plan --json", "benchctl start"])
    return {"state": state, "clusters": clusters(spec, sim, k8s_nodes), "endpoints": endpoints(entities),
            "faults_active": [{"target": f["target"], "label": short(f["label"], 48)} for f in faults],
            "scenarios": scenario_rows(scenarios, plan), "fault_targets": fault_targets, "next": nxt}
