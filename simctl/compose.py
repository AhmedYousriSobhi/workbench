"""Generate the Docker Compose file from sim.yaml and a profile (SPEC section 3)."""
from pathlib import Path

import yaml

from . import slurmconf
from .nodes import node_names

OBSERVABILITY = "../observability.yml"  # static; hosts prometheus, grafana, gpu-exporter-farm

# group -> service -> (image, mem_mb, priority class). Images are built from images/ and components/.
CATALOG: dict[str, dict[str, tuple[str, int, int]]] = {
    "core": {"slurmctld": ("hpcsim/slurm-ctl:dev", 200, 1)},  # also hosts slurmrestd later
    "accounting": {"mariadb": ("mariadb:11", 150, 2), "slurmdbd": ("hpcsim/slurm-ctl:dev", 80, 2)},
    "mocks": {
        "bmc-farm": ("hpcsim/bmc-farm:dev", 100, 2),
        "bcm-mock": ("hpcsim/bcm-mock:dev", 100, 2),
        "jira-mock": ("hpcsim/jira-mock:dev", 100, 2),
    },
    "scenarios": {"scenario-engine": ("hpcsim/scenario-engine:dev", 60, 5)},
    "load": {"user-sim": ("hpcsim/user-sim:dev", 60, 5), "ticket-bot": ("hpcsim/ticket-bot:dev", 40, 5)},
}
SLURMD_MB = 60
SLURMD_PRIO = 4


def load_profile(name: str, root: Path = Path("profiles")) -> dict:
    """Profile with `extends` resolved: groups are the union, other keys the most specific."""
    cfg = yaml.safe_load((root / f"{name}.yaml").read_text())
    merged: dict = {"groups": []}
    for parent in cfg.get("extends") or []:
        base = load_profile(parent, root)
        merged = {**merged, **base, "groups": merged["groups"] + [g for g in base["groups"]
                                                                  if g not in merged["groups"]]}
    own = {k: v for k, v in cfg.items() if k != "extends"}
    groups = merged["groups"] + [g for g in own.get("groups", []) if g not in merged["groups"]]
    return {**merged, **own, "groups": groups}


def _service(image: str, mem_mb: int, prio: int, build: str | None = None) -> dict:
    svc: dict = {"image": image, "mem_limit": f"{mem_mb}m", "cgroup_parent": f"hpcsim-p{prio}.slice",
                 "labels": {"hpcsim.class": f"P{prio}"}, "restart": "unless-stopped"}
    if build:
        svc["build"] = build
    return svc


def generate(sim: dict, profile: dict) -> dict:
    services: dict = {}
    for group in profile["groups"]:
        for name, (image, mem, prio) in CATALOG.get(group, {}).items():
            build = f"../../components/{name}" if (Path("components") / name).is_dir() else None
            services[name] = _service(image, mem, prio, build)
    for name, kind in node_names(sim, profile.get("compute_nodes")):
        svc = _service("hpcsim/node-base:dev", SLURMD_MB, SLURMD_PRIO, "../../images/node-base")
        svc["hostname"] = name
        svc["labels"]["hpcsim.node"] = kind
        if kind == "gpu":
            svc["environment"] = {"HPCSIM_GPUS": str(sim["gpu"]["gpus_per_node"])}
        if "slurmctld" in services:
            svc["depends_on"] = ["slurmctld"]
        services[name] = svc
    slurm_mount = "./slurm:/etc/slurm:ro"
    for name, svc in services.items():
        if name == "slurmctld" or svc["labels"].get("hpcsim.node"):
            svc["volumes"] = [slurm_mount]
            svc["hostname"] = name
    return {"name": "hpcsim", "include": [OBSERVABILITY], "services": services}


def budget_mb(compose: dict) -> int:
    """Sum of the generated services' mem_limit (the static observability file is not counted)."""
    return sum(int(s["mem_limit"].rstrip("m")) for s in compose["services"].values())


def write(compose: dict, out: Path = Path("compose/generated/docker-compose.yml"),
          sim: dict | None = None, compute_nodes: int | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if sim is not None:
        slurmconf.write(sim, out.parent / "slurm", compute_nodes)
    out.write_text("# GENERATED from sim.yaml by `simctl compose`; do not edit.\n"
                   + yaml.safe_dump(compose, sort_keys=False))
    return out
