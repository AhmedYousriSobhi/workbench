"""Kueue without a controller (issue 469, ADR 0034 addendum): the objects in k8s/kueue/queues.yaml and their status.

The definitions are Kueue v0.20.6's v1beta2 only (k8s/kueue/crds.yaml), so no conversion webhook is needed. The status of a
custom resource with a status subresource is ignored by a normal apply, so it is written with `kubectl patch --subresource=status`.
"""
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

SRC = Path(__file__).resolve().parent.parent / "k8s" / "kueue"
CRDS = ("clusterqueues.kueue.x-k8s.io", "localqueues.kueue.x-k8s.io", "resourceflavors.kueue.x-k8s.io")
PLURAL = {"ClusterQueue": "clusterqueues.v1beta2.kueue.x-k8s.io", "LocalQueue": "localqueues.v1beta2.kueue.x-k8s.io"}
STOPPED = {"reason": "Stopped", "message": "Can't admit new workloads: ClusterQueue is stopped"}


def objects(path: Path | None = None) -> list[dict]:
    return [d for d in yaml.safe_load_all((path or SRC / "queues.yaml").read_text()) if d]


def cluster_queues(objs: list[dict] | None = None) -> list[str]:
    return [o["metadata"]["name"] for o in objs or objects() if o["kind"] == "ClusterQueue"]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamped(status: dict) -> dict:
    now = _now()
    return {**status, "conditions": [{**c, "lastTransitionTime": now, "observedGeneration": 1} for c in status.get("conditions", [])]}


def status_calls(objs: list[dict] | None = None) -> list[list[str]]:
    """kubectl arguments that write each object's `status` (objects without one are skipped)."""
    out = []
    for o in objs or objects():
        if o["kind"] in PLURAL and o.get("status"):
            ns = ["-n", o["metadata"]["namespace"]] if o["metadata"].get("namespace") else []
            out.append([*ns, "patch", PLURAL[o["kind"]], o["metadata"]["name"], "--subresource=status", "--type=merge",
                        "-p", json.dumps({"status": _stamped(o["status"])})])
    return out


def queue_problems(q) -> list[str]:
    if not isinstance(q, dict) or q.get("name") not in cluster_queues():
        return [f"k8s queue needs a name of one of: {', '.join(cluster_queues())}"]
    out = []
    if "active" in q and not isinstance(q["active"], bool):
        out.append("k8s queue active is true or false")
    if "pending" in q and (not isinstance(q["pending"], int) or isinstance(q["pending"], bool) or q["pending"] < 0):
        out.append("k8s queue pending is a whole number")
    return out


def queue_calls(q: dict) -> list[list[str]]:
    """The patches for a scenario step `queue: {name, active, pending}`: the ClusterQueue and every LocalQueue that points at it."""
    objs = objects()
    out = []
    for o in objs:
        name = o["metadata"]["name"]
        if o["kind"] == "ClusterQueue" and name == q["name"]:
            kind, ok_msg = "ClusterQueue", "Can admit new workloads"
        elif o["kind"] == "LocalQueue" and o["spec"]["clusterQueue"] == q["name"]:
            kind, ok_msg = "LocalQueue", "Can submit new workloads to clusterQueue"
        else:
            continue
        st: dict = {}
        if "active" in q:
            st["conditions"] = [{"type": "Active", "status": "True", "reason": "Ready", "message": ok_msg} if q["active"] else
                                {"type": "Active", "status": "False",
                                 **(STOPPED if kind == "ClusterQueue" else {"reason": "ClusterQueueIsInactive", "message": "Can't submit new workloads to clusterQueue"})}]
        if "pending" in q and kind == "ClusterQueue":
            st["pendingWorkloads"] = q["pending"]
        if st:
            ns = ["-n", o["metadata"]["namespace"]] if o["metadata"].get("namespace") else []
            out.append([*ns, "patch", PLURAL[kind], name, "--subresource=status", "--type=merge", "-p", json.dumps({"status": _stamped(st)})])
    return out
