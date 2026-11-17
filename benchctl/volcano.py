"""Volcano without a controller (issue 765, ADR 0084): the definitions and objects in k8s/volcano/, and the scenario step `volcano`.

The definitions are Volcano v1.16.0's Queue and PodGroup (v1beta1). A Queue has a status subresource, so its status is written with
`kubectl patch --subresource=status`; a PodGroup has none, so its status is part of the object and a scenario patches it as a normal patch.
"""
import json
from pathlib import Path

import yaml

SRC = Path(__file__).resolve().parent.parent / "k8s" / "volcano"
CRDS = ("queues.scheduling.volcano.sh", "podgroups.scheduling.volcano.sh")
QUEUE = "queues.v1beta1.scheduling.volcano.sh"
PODGROUP = "podgroups.v1beta1.scheduling.volcano.sh"
QUEUE_STATES = ("Open", "Closed")
PODGROUP_PHASES = ("Pending", "Running")
QUEUE_STATUS = {"gpu-batch": {"state": "Open", "running": 2, "pending": 5}, "maintenance": {"state": "Closed"}}  # the fixtures' Queue status


def objects(path: Path | None = None) -> list[dict]:
    return [d for d in yaml.safe_load_all((path or SRC / "queues.yaml").read_text()) if d]


def status_calls() -> list[list[str]]:
    """kubectl arguments that write each Queue's `status` (a PodGroup's is stored by the apply)."""
    return [["patch", QUEUE, name, "--subresource=status", "--type=merge", "-p", json.dumps({"status": st})] for name, st in QUEUE_STATUS.items()]


def step_problems(v) -> list[str]:
    if not isinstance(v, dict) or ("queue" in v) == ("podgroup" in v):
        return ["k8s volcano is {queue: NAME, state: Open|Closed} or {podgroup: NAME, namespace: NS, phase: Pending|Running}"]
    if "queue" in v:
        names = [o["metadata"]["name"] for o in objects() if o["kind"] == "Queue"]
        out = [] if v["queue"] in names else [f"k8s volcano queue is one of: {', '.join(names)}"]
        return out + ([] if v.get("state") in QUEUE_STATES else [f"k8s volcano state is {' or '.join(QUEUE_STATES)}"])
    pgs = [(o["metadata"]["namespace"], o["metadata"]["name"]) for o in objects() if o["kind"] == "PodGroup"]
    out = [] if (v.get("namespace"), v["podgroup"]) in pgs else ["k8s volcano podgroup is one of: " + ", ".join(f"{n}/{p}" for n, p in pgs)]
    return out + ([] if v.get("phase") in PODGROUP_PHASES else [f"k8s volcano phase is {' or '.join(PODGROUP_PHASES)}"])


def calls(v: dict) -> list[list[str]]:
    """The kubectl arguments for a validated `volcano` step."""
    if "queue" in v:
        return [["patch", QUEUE, v["queue"], "--subresource=status", "--type=merge", "-p", json.dumps({"status": {"state": v["state"]}})]]
    return [["-n", v["namespace"], "patch", PODGROUP, v["podgroup"], "--type=merge", "-p", json.dumps({"status": {"phase": v["phase"]}})]]
