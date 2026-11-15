"""MIG-style typed GRES (issue 546, ADR 0075): optional, off by default.

`sim.yaml` `gpu.mig: {nodes: [gpu002], profile: 1g, slices: 7}` splits every GPU of the listed GPU nodes into `slices` units. Slurm
then advertises one typed GRES on that node, `gpu:<gpu type>_<profile>:<gpus_per_node x slices>` (for h200 with 8 GPUs: `gpu:h200_1g:56`),
backed by that many stand-in device files, and jobs ask for it with `--gres=gpu:h200_1g:N`. Everything else stays per physical GPU: the
exporter's series, the node health check and the BMC and BCM entries. The inventory records the typed shape (`gpu_mig`)."""
from __future__ import annotations

import re

from . import nodes as nodemod

MAX_SLICES = 7  # what a real MIG-capable GPU offers at most (seven 1g instances)
PROFILE = re.compile(r"[a-z0-9]{1,12}")


class MigError(ValueError):
    pass


def config(sim: dict) -> dict | None:
    """The validated `gpu.mig` section as {nodes, profile, slices}, or None when MIG is off."""
    raw = (sim.get("gpu") or {}).get("mig")
    if not raw:
        return None
    gpu_nodes = [n for n, k in nodemod.hardware_nodes(sim) if k == "gpu"]
    wanted = raw.get("nodes")
    if not isinstance(wanted, list) or not wanted or not all(isinstance(n, str) for n in wanted):
        raise MigError("gpu.mig.nodes must be a list of GPU node names, for example [gpu002]")
    if (bad := [n for n in wanted if n not in gpu_nodes]):
        raise MigError(f"gpu.mig.nodes names {', '.join(bad)}, which is not a GPU node (GPU nodes: {', '.join(gpu_nodes) or 'none'})")
    profile = str(raw.get("profile", "1g"))
    if not PROFILE.fullmatch(profile):
        raise MigError("gpu.mig.profile is lowercase letters and digits only, for example 1g")
    slices = raw.get("slices", MAX_SLICES)
    if not isinstance(slices, int) or isinstance(slices, bool) or not 1 <= slices <= MAX_SLICES:
        raise MigError(f"gpu.mig.slices must be a whole number from 1 to {MAX_SLICES}")
    return {"nodes": list(dict.fromkeys(wanted)), "profile": profile, "slices": slices}


def gres_type(sim: dict) -> str | None:
    """`h200_1g`: the GPU type and the profile, the type name Slurm and jobs use."""
    mig = config(sim)
    return f"{str(sim['gpu']['type']).lower()}_{mig['profile']}" if mig else None


def is_mig(sim: dict, node: str) -> bool:
    mig = config(sim)
    return bool(mig) and node in mig["nodes"]


def units(sim: dict) -> int:
    """Typed GRES units on one MIG node: every GPU split into `slices`."""
    mig = config(sim)
    return int(sim["gpu"]["gpus_per_node"]) * mig["slices"] if mig else 0


def shape(sim: dict, node: str) -> dict | None:
    """What the inventory records for a MIG node, or None for any other."""
    mig = config(sim)
    if not mig or node not in mig["nodes"]:
        return None
    return {"type": gres_type(sim), "profile": mig["profile"], "slices_per_gpu": mig["slices"], "gres_count": units(sim)}
