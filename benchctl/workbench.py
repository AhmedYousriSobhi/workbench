"""Choose the workbench to build by counts and parts, estimate what it needs, and say whether it fits (issue 92).

A `Spec` (from flags or a workbench file) is turned into the same two things the rest of the tool already consumes:
an effective `sim` dict (node counts) and a profile dict (compose groups, minus parts not selected). The estimate is
the sum of the container memory limits the generated compose file would set, plus the always-on observability stack:
an upper bound, which is what the governor's admission also uses."""
import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import compose

SAVED = Path("compose/generated/workbench.yaml")  # the spec `apply`/`up` last built; absent after a plain profile `up`
OBSERVABILITY_MB = 700                            # prometheus 300 + grafana 300 + gpu-exporter 100 (compose/observability.yml)
FLOOR_MB = 1024                                   # MemAvailable that must remain after the start (same floor as preflight)
# part -> (compose group, services it adds; None = the whole group)
PARTS: dict[str, tuple[str, list[str] | None]] = {
    "jira": ("mocks", ["jira-mock"]), "bmc": ("mocks", ["bmc-farm"]), "bcm": ("mocks", ["bcm-mock"]),
    "accounting": ("accounting", None), "scenarios": ("scenarios", None), "load": ("load", None)}
# The one registry of options that change what runs and so what memory it needs. The Settings sheet is drawn from it
# (`catalog`): a new option is an entry here and in PARTS, not new page code.
PART_INFO: dict[str, tuple[str, str]] = {
    "accounting": ("Accounting", "slurmdbd and MariaDB. Needed for sacct and job history; without it sacct says accounting storage is disabled."),
    "jira": ("Jira", "The Jira mock (REST v2 subset)."),
    "bmc": ("BMC farm", "One Redfish endpoint per node: power, thermals, fans."),
    "bcm": ("BCM", "The BCM mock (CMDaemon JSON) and its device list."),
    "scenarios": ("Scenarios", "The scenario engine that plays timed faults."),
    "load": ("Load", "User and ticket simulators that generate jobs and tickets."),
}
DEFAULT_PARTS = ("jira", "bmc", "bcm", "accounting", "scenarios")  # accounting: without slurmdbd, sacct says storage is disabled
DROP_ORDER = ("load", "accounting", "scenarios", "bcm", "jira", "bmc")  # what to suggest removing first
LIMITS = {"cpu_nodes": (0, 64), "gpu_nodes": (0, 16), "gpus_per_node": (1, 8)}


class SpecError(ValueError):
    pass


@dataclass
class Spec:
    name: str = "workbench"
    cpu_nodes: int = 4
    gpu_nodes: int = 2
    gpus_per_node: int = 8
    parts: list[str] = field(default_factory=lambda: list(DEFAULT_PARTS))
    k8s_nodes: int = 0
    bmc_nodes: list[str] | None = None  # nodes that get a BMC address; None = every node (part "bmc")

    def problems(self) -> list[str]:
        out = []
        for key, (lo, hi) in LIMITS.items():
            if not lo <= getattr(self, key) <= hi:
                out.append(f"{key} must be {lo} to {hi}, not {getattr(self, key)}")
        if self.cpu_nodes + self.gpu_nodes < 1:
            out.append("a workbench needs at least one node")
        out += [f"unknown part {p!r}; parts: {', '.join(PARTS)}" for p in self.parts if p not in PARTS]
        if self.bmc_nodes is not None and not all(isinstance(n, str) and re.fullmatch(r"[a-z]{2,8}[0-9]{3}", n) for n in self.bmc_nodes):
            out.append("bmc_nodes must be node names such as cn001, gpu002")
        if self.k8s_nodes:
            out.append("Kubernetes nodes are not built yet (SPEC section 11): use --k8s-nodes 0")
        return out

    def to_dict(self) -> dict:
        return {"name": self.name, "cpu_nodes": self.cpu_nodes, "gpu_nodes": self.gpu_nodes,
                "gpus_per_node": self.gpus_per_node, "parts": sorted(set(self.parts)), "k8s_nodes": self.k8s_nodes,
                "bmc_nodes": None if self.bmc_nodes is None else sorted(set(self.bmc_nodes))}


def defaults(sim: dict) -> Spec:
    """The workbench sim.yaml describes, so a flag only overrides what it names."""
    return Spec(cpu_nodes=sim.get("compute", {}).get("count", 4), gpu_nodes=sim.get("gpu", {}).get("nodes", 2),
                gpus_per_node=sim.get("gpu", {}).get("gpus_per_node", 8))


def from_dict(data: dict, base: Spec) -> Spec:
    unknown = set(data) - set(base.to_dict())
    if unknown:
        raise SpecError(f"unknown field(s) in the workbench file: {', '.join(sorted(unknown))}")
    merged = {**base.to_dict(), **data}
    return Spec(**{**merged, "parts": list(merged["parts"]),
                   "bmc_nodes": None if merged.get("bmc_nodes") is None else list(merged["bmc_nodes"])})


def load_file(path: Path, base: Spec) -> Spec:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as e:
        raise SpecError(f"cannot read {path}: {e}") from e
    if not isinstance(data, dict):
        raise SpecError(f"{path}: expected a mapping of fields (cpu_nodes, gpu_nodes, parts...)")
    return from_dict(data, base)


def parts_in(services: set[str]) -> list[str]:
    """The optional parts whose services are all in `services` (what a build contains)."""
    out = []
    for part, (group, names) in PARTS.items():
        want = names or list(compose.CATALOG[group])
        if all(n in services for n in want):
            out.append(part)
    return out


def catalog(selected: list[str] | None = None) -> list[dict]:
    """Every optional part with its label, help, memory limit (MB) and whether it is on, for the Settings sheet."""
    on = set(DEFAULT_PARTS if selected is None else selected)
    return [{"id": p, "label": PART_INFO[p][0], "help": PART_INFO[p][1], "mb": _part_mb(p), "on": p in on} for p in PARTS]


def effective(sim: dict, spec: Spec) -> tuple[dict, dict]:
    """(sim, profile) for the spec. Partitions with no nodes are dropped, so slurm.conf never names an empty one."""
    s = copy.deepcopy(sim)
    s.setdefault("compute", {})["count"] = spec.cpu_nodes
    s.setdefault("gpu", {}).update({"nodes": spec.gpu_nodes, "gpus_per_node": spec.gpus_per_node})
    have = {"compute": spec.cpu_nodes, "gpu": spec.gpu_nodes}
    if spec.bmc_nodes is not None:
        s["bmc"] = {**(s.get("bmc") or {}), "nodes": sorted(set(spec.bmc_nodes))}
    s["partitions"] = [p for p in s.get("partitions", []) if have.get(p["nodes"], 1) > 0]
    groups, drop = ["core", "access"], []
    for part in spec.parts:
        group, services = PARTS[part]
        if group not in groups:
            groups.append(group)
    for part, (group, services) in PARTS.items():
        if services and part not in spec.parts:
            drop += services
    return s, {"name": spec.name, "groups": groups, "drop": drop}


def _part_mb(part: str) -> int:
    group, services = PARTS[part]
    return sum(mb for n, (_, mb, _) in compose.CATALOG[group].items() if services is None or n in services)


def estimate(sim: dict, profile: dict) -> dict:
    """Memory limits in MB by what they pay for. An upper bound, like the profile budgets."""
    data = compose.generate(sim, profile)
    nodes = {n: int(s["mem_limit"].rstrip("m")) for n, s in data["services"].items() if s["labels"].get("workbench.node")}
    services = {n: int(s["mem_limit"].rstrip("m")) for n, s in data["services"].items() if n not in nodes}
    total = sum(nodes.values()) + sum(services.values()) + OBSERVABILITY_MB
    return {"nodes_mb": sum(nodes.values()), "node_count": len(nodes), "services_mb": services,
            "observability_mb": OBSERVABILITY_MB, "total_mb": total}


def verdict(est: dict, available_mb: int, floor_mb: int = FLOOR_MB, spec: Spec | None = None) -> tuple[bool, str]:
    """Does it fit, leaving the safety floor? When not, say how much to remove and what that could be."""
    left = available_mb - est["total_mb"]
    if left >= floor_mb:
        return True, f"fits: needs {est['total_mb']} MB, {available_mb} MB free, {left} MB would remain (floor {floor_mb} MB)"
    over = floor_mb - left
    per_node = est["nodes_mb"] // max(est["node_count"], 1)
    hints = [f"{-(-over // per_node)} fewer node(s) (each {per_node} MB)"] if per_node else []
    if spec:  # the two biggest cheap-to-lose parts first, then the node count
        hints = [f"drop {p} ({_part_mb(p)} MB)" for p in DROP_ORDER if p in spec.parts][:2] + hints
    return False, (f"does not fit: needs {est['total_mb']} MB, {available_mb} MB free, short by {over} MB "
                   f"(floor {floor_mb} MB). Try: {'; '.join(hints[:3]) or 'free memory first'}")


def render(spec: Spec, est: dict) -> str:
    rows = [f"Workbench:   {spec.name}",
            f"Nodes:       {spec.cpu_nodes} cpu, {spec.gpu_nodes} gpu x {spec.gpus_per_node} GPUs",
            f"Parts:       {', '.join(sorted(set(spec.parts))) or 'none'} (always: slurm, login node, Prometheus, Grafana)",
            "", "Memory limits (an upper bound, what the governor admits against):",
            f"  {est['node_count']:>3} nodes          {est['nodes_mb']:>6} MB"]
    rows += [f"  {n:<16} {mb:>6} MB" for n, mb in sorted(est["services_mb"].items())]
    rows += [f"  observability    {est['observability_mb']:>6} MB", f"  total            {est['total_mb']:>6} MB"]
    return "\n".join(rows)


def save(spec: Spec, path: Path = SAVED) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(spec.to_dict(), sort_keys=False))


def forget(path: Path = SAVED) -> None:
    path.unlink(missing_ok=True)


def current(sim: dict, path: Path = SAVED) -> Spec | None:
    """The workbench last built by `apply`/`up` with counts, or None when a plain profile is in use."""
    return load_file(path, defaults(sim)) if path.is_file() else None


def live_sim_path(base: Path = Path("sim.yaml"), generated: Path = Path("compose/generated/sim.yaml")) -> Path:
    """The sim file describing what is built: the generated copy while a workbench is in use, else sim.yaml."""
    return generated if SAVED.is_file() and generated.is_file() else base
