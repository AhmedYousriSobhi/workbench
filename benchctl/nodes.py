"""Node naming shared by every generator; must match components/gpu-exporter-farm."""


def node_names(sim: dict, compute_override: int | None = None) -> list[tuple[str, str]]:
    """[(name, kind)] with the same naming as components/gpu-exporter-farm."""
    c, g = sim.get("compute", {}), sim.get("gpu", {})
    count = compute_override if compute_override is not None else c.get("count", 0)
    names = [(f"{c['prefix']}{i:03d}", "compute") for i in range(1, count + 1)]
    names += [(f"{g['prefix']}{i:03d}", "gpu") for i in range(1, g.get("nodes", 0) + 1)]
    return names


# The BMC rule groups: which kind of node a rule names (`bmc.groups` in sim.yaml, --bmc-groups).
GROUPS = ("slurm-cpu", "slurm-gpu", "k8s-cpu", "k8s-gpu")
KIND_GROUP = {"compute": "slurm-cpu", "gpu": "slurm-gpu", "k8s-cpu": "k8s-cpu", "k8s-gpu": "k8s-gpu"}


def k8s_enabled(sim: dict) -> bool:
    """The Kubernetes part is part of this simulation (`effective()` sets `kubernetes.enabled`)."""
    return bool((sim.get("kubernetes") or {}).get("enabled"))


def hardware_nodes(sim: dict, compute_override: int | None = None) -> list[tuple[str, str]]:
    """[(name, kind)] of every machine with hardware: the Slurm nodes (kind compute, gpu), then the Kubernetes nodes
    (k8s-cpu, k8s-gpu) when that part is on. BMC, BCM and the GPU exporter list these; Slurm and Teleport keep `node_names`."""
    out = node_names(sim, compute_override)
    if k8s_enabled(sim):
        from . import k8s

        out += [(n, "k8s-gpu" if g.get("gpus") else "k8s-cpu") for n, g in k8s.node_list(sim)]
    return out
