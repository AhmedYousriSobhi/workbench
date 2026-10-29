"""Generate the Docker Compose file from sim.yaml and a profile (SPEC section 3)."""
from pathlib import Path

import yaml

from . import slurmconf
from .nodes import node_names

OBSERVABILITY = "../observability.yml"  # static; hosts prometheus, grafana, gpu-exporter-farm

# group -> service -> (image, mem_mb, priority class). Images are built from images/ and components/.
CATALOG: dict[str, dict[str, tuple[str, int, int]]] = {
    "core": {"slurmctld": ("hpcsim/slurm-ctl:dev", 200, 1), "slurmrestd": ("hpcsim/slurm-ctl:dev", 60, 1)},
    # mariadb's 100m tmpfs data dir counts against its cgroup, so the limit covers tmpfs + server.
    "accounting": {"mariadb": ("mariadb:11", 250, 2), "slurmdbd": ("hpcsim/slurm-ctl:dev", 80, 2),
                   "accounting-init": ("hpcsim/slurm-ctl:dev", 30, 2)},
    "mocks": {
        "bmc-farm": ("hpcsim/bmc-farm:dev", 100, 2),
        "bcm-mock": ("hpcsim/bcm-mock:dev", 100, 2),
        "jira-mock": ("hpcsim/jira-mock:dev", 100, 2),
    },
    "access": {"login": ("hpcsim/login:dev", 40, 2)},
    "scenarios": {"scenario-engine": ("hpcsim/scenario-engine:dev", 60, 5)},
    "load": {"user-sim": ("hpcsim/user-sim:dev", 60, 5), "ticket-bot": ("hpcsim/ticket-bot:dev", 40, 5)},
}
SSH_PORT = 2222
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
            build = next((f"../../{d}/{name}" for d in ("components", "images") if (Path(d) / name).is_dir()), None)
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
        if name in ("slurmctld", "slurmrestd", "login") or svc["labels"].get("hpcsim.node"):
            svc["volumes"] = [slurm_mount]
            svc["hostname"] = name
            svc.setdefault("environment", {})["HPCSIM_USERS"] = users_env(sim)
    if "login" in services:  # SSH for clients such as Gate-H: localhost only, keys from ./secrets/ssh
        services["login"]["volumes"].append("./secrets/ssh:/run/hpcsim/ssh:ro")
        services["login"].update({"ports": [f"127.0.0.1:{SSH_PORT}:22"],
                                  "depends_on": ["slurmctld"] if "slurmctld" in services else []})
    if "bmc-farm" in services:  # Redfish endpoints for every node in sim.yaml, localhost only
        services["bmc-farm"].update({"volumes": ["../../sim.yaml:/sim.yaml:ro"], "ports": ["127.0.0.1:8000:8000"]})
    if "bcm-mock" in services:  # CMDaemon JSON API on the same node inventory, localhost only
        services["bcm-mock"].update({"volumes": ["../../sim.yaml:/sim.yaml:ro"], "ports": ["127.0.0.1:8081:8081"]})
    if "jira-mock" in services:
        services["jira-mock"].update({"ports": ["127.0.0.1:8080:8080"]})
    _wire_slurm_services(services)
    return {"name": "hpcsim", "include": [OBSERVABILITY], "services": services}


def users_env(sim: dict) -> str:
    """'alice:1001 bob:1002': same names and UIDs on every Slurm container."""
    return " ".join(f"{u['name']}:{1001 + i}" for i, u in enumerate(sim.get("users", [])))


def _wire_slurm_services(services: dict) -> None:
    """Commands, mounts and ordering for slurmrestd and the accounting stack (secrets stay in ./secrets)."""
    if "slurmrestd" in services:
        r = services["slurmrestd"]
        # slurmrestd refuses to run as root or SlurmUser; JWT auth, reachable on the internal network only.
        r["command"] = ["su", "-s", "/bin/sh", "slurmrestd", "-c", "exec /opt/slurm/sbin/slurmrestd -a rest_auth/jwt 0.0.0.0:6820"]
        # Docker's seccomp profile forbids the unshare(2) calls slurmrestd makes by default (SysV sem, fds); skipping just
        # those steps beats loosening seccomp (ADR 0003). The container still has its own IPC namespace.
        r["environment"]["SLURM_JWT"] = "daemon"  # JWT-only mode: accept per-request tokens
        r["environment"]["SLURMRESTD_SECURITY"] = "disable_unshare_sysv,disable_unshare_files"
        r["depends_on"] = ["slurmctld"] if "slurmctld" in services else []
    if "mariadb" not in services:
        return
    services["mariadb"].update({
        "env_file": ["./secrets/mariadb.env"],
        "command": ["--innodb-buffer-pool-size=32M", "--innodb-log-file-size=8M", "--max-connections=20",
                    "--performance-schema=OFF"],
        "tmpfs": ["/var/lib/mysql:size=100m"],
        "healthcheck": {"test": ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"],
                        "interval": "3s", "timeout": "3s", "retries": 30},
    })
    db = services["slurmdbd"]
    db["hostname"] = "slurmdbd"
    db["volumes"] = ["./secrets/slurmdbd:/run/hpcsim/etc:ro"]
    # slurmdbd insists its config is owned by SlurmUser and mode 0600, so install a copy at start.
    install = ("cp /run/hpcsim/etc/* /etc/slurm/ && chown slurm:slurm /etc/slurm/slurmdbd.conf"
               " && chmod 600 /etc/slurm/slurmdbd.conf && exec slurmdbd -D")
    db["command"] = ["sh", "-c", install]
    db["depends_on"] = {"mariadb": {"condition": "service_healthy"}}
    init = services["accounting-init"]
    init["volumes"] = ["./slurm:/etc/slurm:ro", "./slurm/accounting.sh:/accounting.sh:ro"]
    init["command"] = ["sh", "/accounting.sh"]
    init["restart"] = "no"
    init["depends_on"] = ["slurmdbd"]
    if "slurmctld" in services:
        services["slurmctld"]["depends_on"] = ["slurmdbd"]


def budget_mb(compose: dict) -> int:
    """Sum of the generated services' mem_limit (the static observability file is not counted)."""
    return sum(int(s["mem_limit"].rstrip("m")) for s in compose["services"].values())


def write(compose: dict, out: Path = Path("compose/generated/docker-compose.yml"),
          sim: dict | None = None, compute_nodes: int | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if sim is not None:
        slurmconf.write(sim, out.parent / "slurm", compute_nodes, "mariadb" in compose["services"])
    out.write_text("# GENERATED from sim.yaml by `simctl compose`; do not edit.\n"
                   + yaml.safe_dump(compose, sort_keys=False))
    return out
