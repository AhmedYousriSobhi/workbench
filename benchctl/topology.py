"""Real Slurm topology from sim.yaml (issue 543, config lab A-5): `topology.conf` for TopologyPlugin tree or block.

    topology: {plugin: tree, spines: 2, leaves_per_spine: 2}     # block: also block_size (default: the largest power of two every block can fill)

Compute and GPU nodes (the inventory's Slurm nodes, benchctl/hwinventory.py) are spread round-robin over the leaves, so every leaf holds a
mix and a leaf going down is a rack failure, not a partition. With no `topology:` key nothing changes (Slurm's flat default).
"""
from __future__ import annotations

from .nodes import node_names


class TopologyError(ValueError):
    pass


PLUGINS = ("tree", "block")
MAX_SPINES, MAX_LEAVES = 8, 16


def settings(sim: dict) -> dict | None:
    t = sim.get("topology")
    return None if not t else {"plugin": "tree", "spines": 1, "leaves_per_spine": 2, **t}


def slurm_nodes(sim: dict, compute_override: int | None = None) -> list[str]:
    return [n for n, k in node_names(sim, compute_override) if k in ("compute", "gpu")]


def problems(sim: dict, compute_override: int | None = None) -> list[str]:
    t = settings(sim)
    if t is None:
        return []
    out = []
    if t["plugin"] not in PLUGINS:
        out.append(f"topology.plugin must be one of {', '.join(PLUGINS)}, not {t['plugin']!r}")
    for key, top in (("spines", MAX_SPINES), ("leaves_per_spine", MAX_LEAVES)):
        if not isinstance(t[key], int) or isinstance(t[key], bool) or not 1 <= t[key] <= top:
            out.append(f"topology.{key} must be a whole number from 1 to {top}")
    if out:
        return out
    n, count = len(slurm_nodes(sim, compute_override)), t["spines"] * t["leaves_per_spine"]
    if n < count:
        out.append(f"topology has {count} leaves but only {n} Slurm nodes; every leaf needs a node (lower spines or leaves_per_spine, or add nodes)")
    elif t["plugin"] == "block" and "block_size" in t and (not isinstance(t["block_size"], int) or not 1 <= t["block_size"] <= n // count):
        out.append(f"topology.block_size must be a whole number from 1 to {n // count} (the size of the smallest block)")
    return out


def leaves(sim: dict, compute_override: int | None = None) -> dict[str, list[str]]:
    """{leaf name: nodes}: leaf1.. in order; node i goes to leaf (i mod number of leaves). Empty when there is no valid topology."""
    t = settings(sim)
    if t is None or problems(sim, compute_override):
        return {}
    names = [f"leaf{i}" for i in range(1, t["spines"] * t["leaves_per_spine"] + 1)]
    out: dict[str, list[str]] = {n: [] for n in names}
    for i, node in enumerate(slurm_nodes(sim, compute_override)):
        out[names[i % len(names)]].append(node)
    return out


def nodes_under(sim: dict, leaf: str, compute_override: int | None = None) -> list[str]:
    return leaves(sim, compute_override).get(leaf, [])


def hostlist(names: list[str]) -> str:
    """cn001, cn002, cn004, gpu001 -> 'cn[001-002,004],gpu001' (Slurm's hostlist syntax)."""
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(n.rstrip("0123456789"), []).append(n)
    parts = []
    for prefix, members in groups.items():
        if len(members) == 1:
            parts.append(members[0])
        else:
            parts.append(f"{prefix}[{','.join(_runs([m[len(prefix):] for m in members]))}]")
    return ",".join(parts)


def _runs(nums: list[str]) -> list[str]:
    out, start, prev = [], nums[0], nums[0]
    for x in [*nums[1:], None]:
        if x is not None and len(x) == len(prev) and int(x) == int(prev) + 1:
            prev = x
            continue
        out.append(start if start == prev else f"{start}-{prev}")
        if x is not None:
            start = prev = x
    return out


def block_size(sim: dict, compute_override: int | None = None) -> int:
    t = settings(sim)
    if "block_size" in t:
        return t["block_size"]
    smallest, size = min(len(v) for v in leaves(sim, compute_override).values()), 1
    while size * 2 <= smallest:
        size *= 2
    return size


def conf(sim: dict, compute_override: int | None = None) -> str:
    """The text of topology.conf ("" with no topology). tree: one switch per leaf, spines above, a root over several spines. block: one block per leaf."""
    t, groups = settings(sim), leaves(sim, compute_override)
    if t is None or not groups:
        return ""
    head = "# GENERATED from sim.yaml `topology:` by `benchctl compose`; do not edit.\n"
    if t["plugin"] == "block":
        return head + "".join(f"BlockName={leaf} Nodes={hostlist(nodes)}\n" for leaf, nodes in groups.items()) + f"BlockSizes={block_size(sim, compute_override)}\n"
    per, names = t["leaves_per_spine"], list(groups)
    lines = [f"SwitchName={leaf} Nodes={hostlist(nodes)}" for leaf, nodes in groups.items()]
    spines = [f"spine{s + 1}" for s in range(t["spines"])]
    lines += [f"SwitchName={spine} Switches={hostlist(names[s * per:(s + 1) * per])}" for s, spine in enumerate(spines)]
    if len(spines) > 1:
        lines.append(f"SwitchName=root Switches={hostlist(spines)}")
    return head + "\n".join(lines) + "\n"


def plugin_line(sim: dict) -> str | None:
    t = settings(sim)
    return f"TopologyPlugin=topology/{t['plugin']}" if t else None


def switch_problems(do: dict, sim: dict) -> list[str]:
    """Problems with a scenario `switch` step: {leaf: leaf2, state: down|up}."""
    groups = leaves(sim)
    if not groups:
        return ["switch steps need a valid `topology:` in sim.yaml"]
    out = []
    if do.get("leaf") not in groups:
        out.append(f"switch needs leaf: one of {', '.join(groups)}")
    if do.get("state") not in ("down", "up"):
        out.append("switch needs state: down or up")
    return out


def switch_step(do: dict, sim: dict) -> dict:
    """The slurm step a switch step is: a leaf down drains exactly its nodes, up resumes exactly those."""
    nodes = nodes_under(sim, do["leaf"])
    if do["state"] == "down":
        return {"drain": nodes, "reason": f"switch {do['leaf']} down"}
    return {"resume": nodes}
