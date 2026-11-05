"""Choose the workbench to build by counts and parts, estimate what it needs, and say whether it fits (issue 92).

A `Spec` (from flags or a workbench file) is turned into the same two things the rest of the tool already consumes:
an effective `sim` dict (node counts) and a profile dict (compose groups, minus parts not selected). The estimate is
the sum of the container memory limits the generated compose file would set, plus the always-on observability stack:
an upper bound, which is what the governor's admission also uses."""
import copy
import difflib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import compose, logins, memory

SAVED = Path("compose/generated/workbench.yaml")  # the spec `apply`/`up` last built; absent after a plain profile `up`
OBSERVABILITY = {"prometheus": 300, "grafana": 300, "gpu-exporter-farm": 100}  # limits, compose/observability.yml
OBSERVABILITY_MB = sum(OBSERVABILITY.values())
FLOOR_MB = 1024                                   # MemAvailable that must remain after the start (same floor as preflight)
# part -> (compose group, services it adds; None = the whole group)
PARTS: dict[str, tuple[str, list[str] | None]] = {
    "jira": ("mocks", ["jira-mock"]), "jira-real": ("jira-real", None), "bmc": ("mocks", ["bmc-farm"]), "bcm": ("mocks", ["bcm-mock"]),
    "accounting": ("accounting", None), "teleport": ("teleport", None), "scenarios": ("scenarios", None), "load": ("load", None)}
# The one registry of options that change what runs and so what memory it needs. The Cluster architecture sheet is drawn from it
# (`catalog`): a new option is an entry here and in PARTS, not new page code.
PART_INFO: dict[str, tuple[str, str]] = {
    "accounting": ("Accounting", "slurmdbd and MariaDB. Needed for sacct and job history; without it sacct says accounting storage is disabled."),
    "jira": ("Jira", "The Jira mock (REST v2 subset)."),
    "jira-real": ("Jira (real)", "The official Jira Data Center image with Postgres, about 2.4 GB, a 3-hour test licence (ADR 0026). Next to the mock, not instead of it."),
    "bmc": ("BMC farm", "One Redfish endpoint per node: power, thermals, fans."),
    "bcm": ("BCM", "The BCM mock (CMDaemon JSON) and its device list."),
    "teleport": ("Teleport", "Teleport auth and proxy: tsh login and ssh to the login node and every node through it (not on by default)."),
    "scenarios": ("Scenarios", "The scenario engine that plays timed faults."),
    "load": ("Load", "User and ticket simulators that generate jobs and tickets."),
}
# What a user picks by name (`--ticketing jira`, `--hardware bmc,bcm`, ...): category -> (what it groups, {item: part it turns on}).
# An item whose part is None is always on (accepted, nothing to add); NOT_BUILT items are refused with a message.
# Every optional part is in exactly one category (tested), so a new part is added here and in PARTS.
CATEGORIES: dict[str, tuple[str, dict[str, str | None]]] = {
    "workload": ("what runs the jobs", {"slurm": None, "kubernetes": None}),
    "accounting": ("job history for sacct", {"slurmdbd": "accounting"}),
    "monitoring": ("metrics and dashboards", {"prometheus": None, "grafana": None}),
    "ticketing": ("ticket systems", {"jira": "jira", "jira-real": "jira-real"}),
    "hardware": ("out-of-band management", {"bmc": "bmc", "bcm": "bcm"}),
    "access": ("how users reach the nodes", {"teleport": "teleport"}),
    "simulation": ("fault and user simulators", {"scenarios": "scenarios", "load": "load"}),
}
NOT_BUILT = {"kubernetes": "Kubernetes is not built yet (SPEC section 11); use --workload slurm"}
PART_CATEGORY = {part: cat for cat, (_, items) in CATEGORIES.items() for part in items.values() if part}
BY_NAME_ONLY = ("jira-real",)  # heavy (about 2.4 GB): never turned on by `all` or `--addons`, only by its name (ADR 0026)
DEFAULT_PARTS = ("jira", "bmc", "bcm", "accounting", "scenarios")  # accounting: without slurmdbd, sacct says storage is disabled
DROP_ORDER = ("jira-real", "load", "accounting", "scenarios", "bcm", "jira", "bmc")  # what to suggest removing first
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
    login_nodes: list[str] = field(default_factory=lambda: list(logins.DEFAULT))  # auth mode of each login node (ADR 0023)

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
        try:
            modes = logins.parse(self.login_nodes)
            out += logins.problems({"login": modes}, set(self.parts))
        except logins.LoginError as e:
            out.append(str(e))
        if self.k8s_nodes:
            out.append("Kubernetes nodes are not built yet (SPEC section 11): use --k8s-nodes 0")
        return out

    def to_dict(self) -> dict:
        return {"name": self.name, "cpu_nodes": self.cpu_nodes, "gpu_nodes": self.gpu_nodes,
                "gpus_per_node": self.gpus_per_node, "parts": sorted(set(self.parts)), "k8s_nodes": self.k8s_nodes,
                "bmc_nodes": None if self.bmc_nodes is None else sorted(set(self.bmc_nodes)),
                "login_nodes": list(self.login_nodes)}


def items(category: str) -> list[str]:
    """The names a category flag accepts (what Tab offers): the buildable items, then `all`."""
    return [i for i in CATEGORIES[category][1] if i not in NOT_BUILT] + ["all"]


def expand(picked: dict[str, list[str] | None], addons: bool = False) -> list[str]:
    """The parts that category flags (`{"hardware": ["bmc,bcm"], ...}`) and `--addons` turn on. Values may be comma
    separated; `all` takes every item of the category. Raises SpecError with one message for the first bad item."""
    parts: list[str] = [p for p in PARTS if p not in BY_NAME_ONLY] if addons else []
    for cat, values in picked.items():
        every = [i for i in CATEGORIES[cat][1] if i not in NOT_BUILT]
        all_of = [i for i in every if CATEGORIES[cat][1][i] not in BY_NAME_ONLY]  # what `all` takes
        for item in (v.strip() for val in (values or []) for v in val.split(",") if v.strip()):
            if item in NOT_BUILT:
                raise SpecError(NOT_BUILT[item])
            if item != "all" and item not in CATEGORIES[cat][1]:
                close = difflib.get_close_matches(item, every, n=1)
                hint = f" Did you mean '{close[0]}'?" if close else ""
                raise SpecError(f"unknown {cat} item '{item}'.{hint} Valid: {', '.join(items(cat))}.")
            for i in all_of if item == "all" else [item]:
                if part := CATEGORIES[cat][1][i]:
                    parts.append(part)
    return sorted(set(parts))


def defaults(sim: dict) -> Spec:
    """The workbench sim.yaml describes, so a flag only overrides what it names."""
    return Spec(cpu_nodes=sim.get("compute", {}).get("count", 4), gpu_nodes=sim.get("gpu", {}).get("nodes", 2),
                gpus_per_node=sim.get("gpu", {}).get("gpus_per_node", 8), login_nodes=logins.modes(sim))


def from_dict(data: dict, base: Spec) -> Spec:
    unknown = set(data) - set(base.to_dict())
    if unknown:
        raise SpecError(f"unknown field(s) in the workbench file: {', '.join(sorted(unknown))}")
    merged = {**base.to_dict(), **data}
    return Spec(**{**merged, "parts": list(merged["parts"]),
                   "bmc_nodes": None if merged.get("bmc_nodes") is None else list(merged["bmc_nodes"]),
                   "login_nodes": list(merged["login_nodes"])})


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
    """Every optional part with its label, help, memory limit (MB) and whether it is on, for the Cluster architecture sheet."""
    on = set(DEFAULT_PARTS if selected is None else selected)
    return [{"id": p, "category": PART_CATEGORY[p], "label": PART_INFO[p][0], "help": PART_INFO[p][1], "mb": _part_mb(p), "on": p in on} for p in PARTS]


def effective(sim: dict, spec: Spec) -> tuple[dict, dict]:
    """(sim, profile) for the spec. Partitions with no nodes are dropped, so slurm.conf never names an empty one."""
    s = copy.deepcopy(sim)
    s.setdefault("compute", {})["count"] = spec.cpu_nodes
    s.setdefault("gpu", {}).update({"nodes": spec.gpu_nodes, "gpus_per_node": spec.gpus_per_node})
    have = {"compute": spec.cpu_nodes, "gpu": spec.gpu_nodes}
    if spec.bmc_nodes is not None:
        s["bmc"] = {**(s.get("bmc") or {}), "nodes": sorted(set(spec.bmc_nodes))}
    s["login"] = list(spec.login_nodes)
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


def running_services() -> set[str]:
    """Service names of the workbench containers running now (empty when Docker does not answer)."""
    try:
        r = subprocess.run(["docker", "ps", "--filter", "label=com.docker.compose.project=workbench", "--format",
                            '{{.Label "com.docker.compose.service"}}'], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(r.stdout.split())


def estimate(sim: dict, profile: dict, app_mb: int | None = None, running: set[str] | None = None) -> dict:
    """What the selection needs. `total_mb` is the sum of the caps (the container limits); `expected_mb` is what it is expected to
    use (measured plus tolerance, memory.py); `need_mb` is the expected use plus the app under test's reserve, the number that
    admission compares with free memory. Services in `running` are already in the host's used memory, so their expected use
    is left out of `need_mb` (`running_mb`): free memory must cover only what a start would add."""
    data = compose.generate(sim, profile)
    sv = data["services"]
    nodes = {n: int(s["mem_limit"].rstrip("m")) for n, s in sv.items() if s["labels"].get("workbench.node")}
    services = {n: int(s["mem_limit"].rstrip("m")) for n, s in sv.items() if n not in nodes}
    seen = {n: memory.expected_mb(n, mb, sv[n]["labels"]) for n, mb in {**nodes, **services}.items()}
    obs = {n: memory.expected_mb(n, mb) for n, mb in OBSERVABILITY.items()}
    expected = sum(seen.values()) + sum(obs.values())
    app = memory.app_mb() if app_mb is None else app_mb
    up = set(running or ())
    running_mb = sum(v for n, v in {**seen, **obs}.items() if n in up)
    return {"nodes_mb": sum(nodes.values()), "node_count": len(nodes), "services_mb": services,
            "observability_mb": OBSERVABILITY_MB, "total_mb": sum(nodes.values()) + sum(services.values()) + OBSERVABILITY_MB,
            "expected_mb": expected, "expected_nodes_mb": sum(seen[n] for n in nodes),
            "expected_services_mb": {n: seen[n] for n in services}, "expected_observability_mb": sum(obs.values()),
            "app_mb": app, "running_mb": running_mb, "need_mb": expected - running_mb + app}


def verdict(est: dict, available_mb: int, floor_mb: int = FLOOR_MB, spec: Spec | None = None) -> tuple[bool, str]:
    """Does it fit, leaving the safety floor? When not, say how much to remove and what that could be."""
    need = est["need_mb"]
    mix = f"{est['expected_mb']} MB expected" + (f" - {est['running_mb']} MB already running" if est.get("running_mb") else "") + (f" + {est['app_mb']} MB for the app" if est["app_mb"] else "")
    left = available_mb - need
    if left >= floor_mb:
        return True, f"fits: needs {need} MB ({mix}), {available_mb} MB free, {left} MB would remain (floor {floor_mb} MB)"
    over = floor_mb - left
    per_node = est["expected_nodes_mb"] // max(est["node_count"], 1)
    fewer = -(-over // per_node) if per_node else 0
    hints = [f"{fewer} fewer node(s) (each {per_node} MB)"] if 0 < fewer <= est["node_count"] else []
    if spec:  # the two biggest cheap-to-lose parts first, then the node count
        hints = [f"drop {p} ({_part_mb(p)} MB)" for p in DROP_ORDER if p in spec.parts][:2] + hints
    if est["app_mb"]:
        hints.append("--app-mb 0 when the app already runs")
    return False, (f"does not fit: needs {need} MB ({mix}), {available_mb} MB free, short by {over} MB "
                   f"(floor {floor_mb} MB). Try: {'; '.join(hints[:3]) or 'free memory first'}")


def render(spec: Spec, est: dict) -> str:
    rows = [f"Workbench:   {spec.name}",
            f"Nodes:       {spec.cpu_nodes} cpu, {spec.gpu_nodes} gpu x {spec.gpus_per_node} GPUs",
            "Login nodes: " + ", ".join(f"{n} ({m})" for n, m in logins.nodes({"login": spec.login_nodes})),
            f"Parts:       {', '.join(sorted(set(spec.parts))) or 'none'} (always: slurm, Prometheus, Grafana)",
            "", f"Memory, MB: expected use (measured + {round(memory.TOLERANCE * 100)}%, memory.py) and the cap the container may reach",
            f"  {'':<18} {'expected':>8} {'cap':>8}",
            f"  {est['node_count']:>3} nodes{'':<10} {est['expected_nodes_mb']:>8} {est['nodes_mb']:>8}"]
    rows += [f"  {n:<18} {est['expected_services_mb'][n]:>8} {mb:>8}" for n, mb in sorted(est["services_mb"].items())]
    rows += [f"  {'observability':<18} {est['expected_observability_mb']:>8} {est['observability_mb']:>8}",
             f"  {'total':<18} {est['expected_mb']:>8} {est['total_mb']:>8}"]
    if est.get("running_mb"):
        rows += [f"  {'already running':<18} {-est['running_mb']:>8}   (already in the used memory)"]
    if est["app_mb"]:
        rows += [f"  {'app under test':<18} {est['app_mb']:>8}"]
    if est["app_mb"] or est.get("running_mb"):
        rows += [f"  {'needs':<18} {est['need_mb']:>8}   (admitted against this)"]
    return "\n".join(rows)


def save(spec: Spec, path: Path = SAVED) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(spec.to_dict(), sort_keys=False))


def forget(path: Path = SAVED) -> None:
    path.unlink(missing_ok=True)


def current(sim: dict, path: Path = SAVED) -> Spec | None:
    """The workbench last built by `apply`/`up` with counts, or None when a plain profile is in use."""
    return load_file(path, defaults(sim)) if path.is_file() else None


def start_line(saved: Spec | None, profile: str | None, parts: bool) -> str:
    """The one line `benchctl start` prints first: what it starts, and what saved workbench that replaces (issue 215).

    A start with parts builds the default workbench plus those parts; one without builds the size preset (default min).
    Either way it does not reuse the saved workbench, it forgets it; the plain default is `start --profile min`."""
    what = "the default workbench plus the parts you named" if parts else f"the {profile or 'min'} size preset"
    if saved is None:
        return f"Starting {what}."
    mine = f"{saved.cpu_nodes} compute + {saved.gpu_nodes} GPU nodes, parts {', '.join(saved.parts) or 'none'}"
    return (f"Starting {what}. This replaces the workbench last built ({mine}); "
            "to rebuild that one, run `benchctl apply -f FILE` or `benchctl up` with its counts.")


def live_sim_path(base: Path = Path("sim.yaml"), generated: Path = Path("compose/generated/sim.yaml")) -> Path:
    """The sim file describing what is built: the generated copy while a workbench is in use, else sim.yaml."""
    return generated if SAVED.is_file() and generated.is_file() else base
