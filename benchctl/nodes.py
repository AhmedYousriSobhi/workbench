"""Node naming shared by every generator; must match components/gpu-exporter-farm."""


def node_names(sim: dict, compute_override: int | None = None) -> list[tuple[str, str]]:
    """[(name, kind)] with the same naming as components/gpu-exporter-farm."""
    c, g = sim.get("compute", {}), sim.get("gpu", {})
    count = compute_override if compute_override is not None else c.get("count", 0)
    names = [(f"{c['prefix']}{i:03d}", "compute") for i in range(1, count + 1)]
    names += [(f"{g['prefix']}{i:03d}", "gpu") for i in range(1, g.get("nodes", 0) + 1)]
    if (sim.get("real_gpu") or {}).get("enabled"):  # the real card's Slurm node (issue 428): Slurm, ssh, Teleport, the BMC farm and the BCM mock have it; the exporter farm does not, the card's own exporter serves it (issue 431)
        names.append((sim["real_gpu"]["node"], "real-gpu"))
    return names


# The BMC rule groups: which kind of node a rule names (`bmc.groups` in sim.yaml, --bmc-groups).
GROUPS = ("slurm-cpu", "slurm-gpu", "k8s-cpu", "k8s-gpu")
KIND_GROUP = {"compute": "slurm-cpu", "gpu": "slurm-gpu", "real-gpu": "slurm-gpu", "k8s-cpu": "k8s-cpu", "k8s-gpu": "k8s-gpu"}


def k8s_enabled(sim: dict) -> bool:
    """The Kubernetes part is part of this simulation (`effective()` sets `kubernetes.enabled`)."""
    return bool((sim.get("kubernetes") or {}).get("enabled"))


def hardware_nodes(sim: dict, compute_override: int | None = None) -> list[tuple[str, str]]:
    """[(name, kind)] of every machine with hardware: the Slurm nodes (kind compute, gpu), then the Kubernetes nodes
    (k8s-cpu, k8s-gpu) when that part is on, then the real card's node (kind real-gpu) when that part is on. BMC and BCM list these
    (the GPU exporter farm skips the real one); Slurm and Teleport keep `node_names`. The real node comes last, so no simulated
    node changes its place, address or BMC port (issue 431)."""
    every = node_names(sim, compute_override)
    out = [x for x in every if x[1] != "real-gpu"]
    if k8s_enabled(sim):
        from . import k8s

        out += [(n, "k8s-gpu" if g.get("gpus") else "k8s-cpu") for n, g in k8s.node_list(sim)]
    return out + [x for x in every if x[1] == "real-gpu"]
