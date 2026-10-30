"""Node naming shared by every generator; must match components/gpu-exporter-farm."""


def node_names(sim: dict, compute_override: int | None = None) -> list[tuple[str, str]]:
    """[(name, kind)] with the same naming as components/gpu-exporter-farm."""
    c, g = sim.get("compute", {}), sim.get("gpu", {})
    count = compute_override if compute_override is not None else c.get("count", 0)
    names = [(f"{c['prefix']}{i:03d}", "compute") for i in range(1, count + 1)]
    names += [(f"{g['prefix']}{i:03d}", "gpu") for i in range(1, g.get("nodes", 0) + 1)]
    return names
