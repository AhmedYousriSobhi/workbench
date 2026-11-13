"""The machine inventory: one generated `inventory.json` that every node-shaped mock reads (issue 462, SPEC section 3).

Slurm, the BMC farm, the BCM mock and the GPU exporter used to parse `sim.yaml` each on their own, and the shapes drifted (a GPU node had 8
CPUs in Slurm and 64 in Redfish). The shape of every machine is decided here, once; the mocks read the file and decide nothing.
`inventory.py` is the other thing: the rows of `benchctl get nodes`.
"""
import json
from pathlib import Path

from . import nodes

FILE = "inventory.json"
VERSION = 1


def gpu_uuid(node: str, index: int) -> str:
    return f"GPU-{node}-{index:02d}"


def serial(node: str) -> str:
    return f"SIM-{node.upper()}"


def gpu_shape(sim: dict) -> dict:
    """CPUs and memory of a Slurm GPU node: `gpu.cpus` and `gpu.memory_mb`, else 8 CPUs and 2048 MB per GPU."""
    g = sim.get("gpu", {})
    n = int(g.get("gpus_per_node", 0))
    return {"cpus": int(g.get("cpus", 8 * n)), "memory_mb": int(g.get("memory_mb", 2048 * n))}


def compute_shape(sim: dict) -> dict:
    c = sim.get("compute", {})
    return {"cpus": int(c.get("cpus", 8)), "memory_mb": int(c.get("memory_mb", 4096))}


def _machine(name: str, kind: str, sim: dict, groups: dict[str, dict]) -> dict:
    if kind == "compute":
        shape, gpus, gpu_type = compute_shape(sim), 0, ""
    elif kind == "gpu":
        shape, gpus, gpu_type = gpu_shape(sim), int(sim["gpu"]["gpus_per_node"]), str(sim["gpu"].get("type", "")).lower()
    else:  # a Kubernetes node: its group in sim.yaml kubernetes.nodes
        g = groups[name]
        shape = {"cpus": int(g.get("cpus", 8)), "memory_mb": int(g.get("memory_gb", 32)) * 1024}
        gpus, gpu_type = int(g.get("gpus", 0)), str(g.get("gpu_type", "")).lower() if g.get("gpus") else ""
    return {"name": name, "kind": kind, **shape, "gpus": gpus, "gpu_type": gpu_type, "serial": serial(name),
            "gpu_uuids": [gpu_uuid(name, i) for i in range(gpus)]}


def build(sim: dict, compute_override: int | None = None) -> dict:
    """Every machine with hardware, in the order the BMC farm numbers them: Slurm compute, Slurm GPU, then Kubernetes nodes when that part is on.
    The real card's node (real_gpu) is not listed: the BMC, BCM and exporter do not have it yet (issue 431)."""
    groups: dict[str, dict] = {}
    if nodes.k8s_enabled(sim):
        from . import k8s

        groups = dict(k8s.node_list(sim))
    return {"version": VERSION,
            "gpu_type": str(sim.get("gpu", {}).get("type", "")).lower(),
            "bcm_manages_kubernetes": bool((sim.get("bcm") or {}).get("manage_kubernetes", True)),  # false: Kubernetes nodes are not BCM devices (issue 413)
            "nodes": [_machine(n, k, sim, groups) for n, k in nodes.hardware_nodes(sim, compute_override)]}


def write(sim: dict, path: Path, compute_override: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build(sim, compute_override), indent=1) + "\n")
    return path
