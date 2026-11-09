"""Generate the Docker Compose file from sim.yaml and a profile (SPEC section 3)."""
import copy
import os
import sys
from pathlib import Path

import yaml

from . import (
    addressing,
    controller,
    dcgm,
    jira_real,
    k8s,
    logins,
    nhc,
    real_gpu,
    slurmconf,
    teleport,
)
from .nodes import KIND_GROUP, hardware_nodes, node_names

OBSERVABILITY = "../observability.yml"  # static; hosts prometheus, grafana, gpu-exporter-farm

# group -> service -> (image, mem_mb, priority class). Images are built from images/ and components/.
CATALOG: dict[str, dict[str, tuple[str, int, int]]] = {
    "core": {"slurmctld": ("workbench/slurm-ctl:dev", 200, 1), "slurmrestd": ("workbench/slurm-ctl:dev", 60, 1)},
    # mariadb's 100m tmpfs data dir counts against its cgroup, so the limit covers tmpfs + server.
    "accounting": {"mariadb": ("mariadb:11", 250, 2), "slurmdbd": ("workbench/slurm-ctl:dev", 80, 2),
                   "accounting-init": ("workbench/slurm-ctl:dev", 30, 2)},
    "mocks": {
        "bmc-farm": ("workbench/bmc-farm:dev", 100, 2),
        "bcm-mock": ("workbench/bcm-mock:dev", 100, 2),
        "jira-mock": ("workbench/jira-mock:dev", 100, 2),
    },
    # real Jira Data Center and its Postgres (ADR 0026): off by default, about 2.4 GB, P6 (the first thing the governor stops)
    "jira-real": {"jira-db": (jira_real.DB_IMAGE, jira_real.DB_LIMIT_MB, 6), "jira-real": (jira_real.IMAGE, jira_real.LIMIT_MB, 6)},
    "access": {"login": ("workbench/login:dev", 40, 2)},
    "dcgm": {dcgm.SERVICE: ("workbench/dcgm-exporter:dev", dcgm.LIMIT_MB, 6)},  # the real DCGM exporter on a mock NVML (issue 426); off by default
    "real-gpu": {real_gpu.SERVICE: ("workbench/dcgm-exporter:dev", real_gpu.LIMIT_MB, 6)},  # the host's own NVIDIA card with the real exporter (issue 428); off by default
    "jump": {"jump": ("workbench/jump:dev", 24, 2)},  # a bare sshd bastion (SPEC C-21)
    # the tag is replaced from sim.yaml teleport.version (benchctl/teleport.py); the limit is a planning estimate until measured
    "teleport": {"teleport": (f"{teleport.IMAGE}:{teleport.DEFAULT_VERSION}", 256, 2)},
    # a real control plane with KWOK nodes (ADR 0034); the image tags are replaced from sim.yaml kubernetes.version (benchctl/k8s.py)
    "k8s": {n: (k8s.images({})[n], k8s.LIMITS[n], 3) for n in k8s.SERVICES},
    "scenarios": {"scenario-engine": ("workbench/scenario-engine:dev", 60, 5)},
    "load": {"user-sim": ("workbench/user-sim:dev", 60, 5), "ticket-bot": ("workbench/ticket-bot:dev", 40, 5)},
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
    svc: dict = {"image": image, "mem_limit": f"{mem_mb}m", "cgroup_parent": f"workbench-p{prio}.slice",
                 "labels": {"workbench.class": f"P{prio}"}, "restart": "unless-stopped"}
    if build:
        svc["build"] = build
    return svc


def generate(sim: dict, profile: dict) -> dict:
    services: dict = {}
    for group in profile["groups"]:
        for name, (image, mem, prio) in CATALOG.get(group, {}).items():
            if name in profile.get("drop", []):  # a part the workbench did not select (benchctl plan/apply)
                continue
            # the build directory is the service name, or the image name (slurmctld and slurmrestd share images/slurm-ctl)
            img = image.split("/")[-1].split(":")[0]
            build = next((f"../../{d}/{n}" for n in (name, img) for d in ("components", "images") if (Path(d) / n).is_dir()), None)
            services[name] = _service(image, mem, prio, build)
    if "login" in services:  # more login nodes are copies of the first (ADR 0023); the image is built once, by `login`
        for i, (lname, _) in enumerate(logins.nodes(sim)):
            if i:
                services[lname] = {k: v for k, v in copy.deepcopy(services["login"]).items() if k != "build"}
    if problems := logins.problems(sim, {p for p, n in (("teleport", "teleport"), ("k8s", k8s.API)) if n in services}):
        raise logins.LoginError("; ".join(problems))
    for name, kind in node_names(sim, profile.get("compute_nodes")):
        svc = _service("workbench/node-base:dev", SLURMD_MB, SLURMD_PRIO, "../../images/node-base")
        svc["hostname"] = name
        svc["labels"]["workbench.node"] = kind
        svc["environment"] = {"WORKBENCH_IB": str(sim["gpu"]["gpus_per_node"] if kind == "gpu" else 0 if kind == "real-gpu" else 1)}  # InfiniBand ports nhc expects
        if kind == "gpu":
            svc["environment"]["WORKBENCH_GPUS"] = str(sim["gpu"]["gpus_per_node"])
        if kind == "real-gpu":  # the real card as a CDI device: unprivileged, no stand-in files (WORKBENCH_GPUS stays 0 so nhc's simulated-GPU checks skip it)
            svc["devices"] = [real_gpu.DEVICE]
        if "slurmctld" in services:
            svc["depends_on"] = ["slurmctld"]
        services[name] = svc
    slurm_mount = "./slurm:/etc/slurm:ro"
    lkinds = logins.kinds(sim) if "login" in services else {}
    for name, svc in services.items():
        if name in ("slurmctld", "slurmrestd") or logins.is_login(name) or svc["labels"].get("workbench.node"):
            svc["volumes"] = [] if lkinds.get(name) == "k8s" else [slurm_mount]  # a k-login has no Slurm configuration: its Slurm tools say so
            if svc["labels"].get("workbench.node"):  # sshd on every compute node (ADR 0014)
                svc["volumes"].append("./nhc:/etc/workbench/nhc:ro")  # the health checks, edited live (ADR 0019)
                svc["volumes"].append("./secrets/ssh:/run/workbench/ssh:ro")
                svc.setdefault("environment", {})["WORKBENCH_SSHD"] = "1"
            svc["hostname"] = name
            svc.setdefault("environment", {})["WORKBENCH_USERS"] = users_env(sim)
    for i, (lname, mode) in enumerate(logins.nodes(sim) if "login" in services else []):
        # SSH for clients such as a monitoring app: localhost only, keys from ./secrets/ssh. A `teleport` login publishes
        # no port and takes no key (login.sh): the proxy is its only way in (ADR 0023)
        svc = services[lname]
        svc["volumes"].append("./secrets/ssh:/run/workbench/ssh:ro")
        svc.setdefault("environment", {})["WORKBENCH_LOGIN_AUTH"] = mode
        svc.setdefault("environment", {})["WORKBENCH_LOGIN_KIND"] = lkinds[lname]
        svc.update({"depends_on": ["slurmctld"] if "slurmctld" in services and lkinds[lname] != "k8s" else []})
        if mode in logins.DIRECT:
            svc["ports"] = [f"127.0.0.1:{SSH_PORT + i}:22"]
    if controller.enabled(sim) and "slurmctld" in services:  # the controller: slurmctld's container also runs sshd for whitelisted users and is the BCM head node (issue 416)
        if problems := controller.problems(sim):
            raise controller.ControllerError("; ".join(problems))
        ctl = services["slurmctld"]
        ctl["mem_limit"] = f"{int(ctl['mem_limit'][:-1]) + controller.EXTRA_MB}m"
        ctl.setdefault("environment", {}).update({"WORKBENCH_SSHD": "1", "WORKBENCH_CONTROLLER": "1", "WORKBENCH_SSH_ALLOW": " ".join(controller.allowed_users(sim)),
                                                  "WORKBENCH_HEAD_IP": addressing.service_ip("slurmctld"),
                                                  "WORKBENCH_TELEPORT_NAME": controller.ALIAS})
        ctl["volumes"] += ["./secrets/ssh:/run/workbench/ssh:ro", "../../images/login/cmsh.py:/usr/local/bin/cmsh:ro"]
        ctl["ports"] = [f"127.0.0.1:{controller.SSH_PORT}:22"]
        ctl.setdefault("networks", {}).setdefault("default", {})["aliases"] = [controller.ALIAS]
        for lname in lkinds:  # the login nodes are no longer the head node: no cmsh there
            services[lname].setdefault("environment", {})["WORKBENCH_HEAD"] = "0"
    if "jump" in services:  # the bastion: sshd only, localhost fallback port, the same users and keys
        services["jump"].update({"hostname": "jump", "ports": [f"127.0.0.1:{controller.JUMP_PORT}:22"],
                                 "environment": {"WORKBENCH_USERS": users_env(sim)}, "volumes": ["./secrets/ssh:/run/workbench/ssh:ro"]})
    if "teleport" in services:  # auth + proxy (ADR 0020): only the proxy and diagnostics ports are published, localhost only
        services["teleport"].update({
            "image": teleport.image(sim),  # its entrypoint already is `teleport start -c /etc/teleport/teleport.yaml`
            "user": f"{os.getuid()}:{os.getgid()}",  # not root; the directories are made by `benchctl up` as this user
            "volumes": ["./teleport:/etc/teleport:ro", "./teleport/data:/var/lib/teleport",
                        f"./secrets/teleport:{teleport.CONTAINER_SECRETS}"],
            "networks": {"default": {"aliases": [teleport.DNS_NAME]}},  # the name resolves inside the cluster network; the IP is added below
            "ports": [f"127.0.0.1:{teleport.PROXY_PORT}:{teleport.PROXY_PORT}",  # a localhost fallback, the address clients are told is the IP
                      f"127.0.0.1:{teleport.DIAG_PORT}:{teleport.DIAG_PORT}"]})
        for svc in services.values():  # sshd trusts the Teleport CA and presents its host certificate (nodes and login)
            if svc["labels"].get("workbench.node") or any(svc is services.get(n) for n in logins.via_teleport(sim)) or (
                    svc is services.get(controller.CONTROLLER) and controller.enabled(sim)):
                svc["volumes"].append(f"./secrets/teleport:{teleport.CONTAINER_SECRETS}:ro")
    dcgm.wire(services, sim)  # no-op unless the dcgm part is on
    real_gpu.wire(services)  # no-op unless the real-gpu part is on
    k8s.wire(services, sim)  # no-op unless the k8s part is on
    if k8s.API in services:  # every login node gets the read-only kubeconfigs (login.sh puts them in each user's ~/.kube)
        for lname in [n for n, k in lkinds.items() if k in ("k8s", "both")]:  # an s-login gets none
            services[lname]["volumes"] += ["./secrets/k8s/viewer.kubeconfig:/run/workbench/k8s/viewer.kubeconfig:ro",
                                           "./secrets/k8s/limited.kubeconfig:/run/workbench/k8s/limited.kubeconfig:ro"]
    if "bmc-farm" in services:  # Redfish endpoints for every node in sim.yaml, localhost only
        # :8443 is TLS at the standard /redfish/v1, the node chosen by Host: clients reach it as bmc-<node>
        services["bmc-farm"].update({
            "volumes": ["./sim.yaml:/sim.yaml:ro", "./secrets/bmc_credentials:/run/workbench/bmc_credentials:ro"],
            "ports": ["127.0.0.1:8000:8000", "127.0.0.1:8443:8443"]})
        _bmc_forwarders(services, sim, profile)
    if "bcm-mock" in services:  # CMDaemon JSON API on the same node inventory, localhost only
        services["bcm-mock"].update({"volumes": ["./sim.yaml:/sim.yaml:ro"], "ports": ["127.0.0.1:8081:8081"]})
    if "jira-mock" in services:
        # a team works the project in the background, one change about every 45 s (benchctl set jira-activity off to stop it, #158)
        services["jira-mock"].update({"ports": ["127.0.0.1:8080:8080"], "environment": {"JIRA_ACTIVITY_EVERY": "45"}})
    if "jira-real" in services:
        _wire_jira_real(services)
    _wire_slurm_services(services)
    ips = addressing.plan(sim, profile.get("compute_nodes"))
    for name, svc in services.items():  # a fixed address for everything (ADR 0018)
        if ip := addressing.address_of(ips, name):
            svc.setdefault("networks", {}).setdefault("default", {})["ipv4_address"] = ip
    nets = {"default": {"ipam": {"config": [{"subnet": addressing.INTERNAL, "gateway": addressing.GATEWAY}]}}}
    if "bmc-farm" in services:
        nets["ipmi"] = {"ipam": {"config": [{"subnet": addressing.IPMI, "gateway": addressing.IPMI_GATEWAY}]}}
    out = {"name": "workbench", "include": [OBSERVABILITY], "networks": nets, "services": services}
    if "jira-real" in services:  # named volumes: the data survives `down`; `benchctl remove jira-real-state` removes them
        out["volumes"] = {v: {} for v in jira_real.VOLUMES}
    return out


BMC_FORWARDER_MB = 32


def _wire_jira_real(services: dict) -> None:
    """The official Jira image on 127.0.0.1 only, with Postgres (Jira 10's wizard has no built-in database). Unprivileged
    and memory-limited like everything else; the heap is capped below the container limit so the JVM is not OOM-killed."""
    services["jira-real"].update({
        "ports": [f"127.0.0.1:{jira_real.PORT}:8080"],
        "environment": {"JVM_MINIMUM_MEMORY": f"{jira_real.HEAP_MIN_MB}m", "JVM_MAXIMUM_MEMORY": f"{jira_real.HEAP_MAX_MB}m",
                        "JVM_RESERVED_CODE_CACHE_SIZE": f"{jira_real.CODE_CACHE_MB}m", "JVM_SUPPORT_RECOMMENDED_ARGS": jira_real.JVM_ARGS},
        "volumes": ["jira-home:/var/atlassian/application-data/jira"],
        "depends_on": {"jira-db": {"condition": "service_healthy"}}})
    services["jira-db"].update({
        "env_file": ["./secrets/jira-db.env"],
        "environment": {"POSTGRES_INITDB_ARGS": "--encoding=UTF8 --lc-collate=C --lc-ctype=C"},  # Jira refuses any other collation
        "command": ["-c", "shared_buffers=64MB", "-c", "max_connections=60"],
        "volumes": ["jira-db:/var/lib/postgresql/data"],
        "healthcheck": {"test": ["CMD-SHELL", f"pg_isready -U {jira_real.DB_USER} -d {jira_real.DB_NAME}"], "interval": "3s", "timeout": "3s", "retries": 30}})


def bmc_nodes(sim: dict, profile: dict | None = None) -> list[str]:
    """The nodes that have a BMC address: the machines whose group `sim["bmc"]["groups"]` names (default every group: Slurm CPU and
    GPU nodes, Kubernetes CPU and GPU nodes), then only those in `sim["bmc"]["nodes"]` when a selection lists some."""
    cfg = sim.get("bmc") or {}
    groups = cfg.get("groups")
    every = [n for n, k in hardware_nodes(sim, (profile or {}).get("compute_nodes")) if groups is None or KIND_GROUP[k] in groups]
    chosen = cfg.get("nodes")
    return every if chosen is None else [n for n in every if n in chosen]


def _bmc_forwarders(services: dict, sim: dict, profile: dict) -> None:
    """One small container per BMC, its own IP on the ipmi network at the standard port (ADR 0018). It forwards to the
    farm, which keeps the state, so a node without a forwarder simply has no BMC address."""
    p = addressing.plan(sim, profile.get("compute_nodes"))
    for n in bmc_nodes(sim, profile):
        port = p["bmc_port"][n]
        svc = _service("python:3.12-slim", BMC_FORWARDER_MB, 2)
        svc.update({"command": ["python", "/app/forward.py", f"443:bmc-farm:{port}", f"8443:bmc-farm:{port}"],
                    "volumes": ["../../components/bmc-farm/forward.py:/app/forward.py:ro"], "depends_on": ["bmc-farm"],
                    "networks": {"ipmi": {"ipv4_address": p["bmc"][n]}}})
        services[f"bmc-{n}"] = svc
    services["bmc-farm"]["networks"] = {"default": {}, "ipmi": {"ipv4_address": addressing.IPMI_FARM_IP}}
    for lname, ip in p["ipmi_logins"].items():  # the head nodes reach the BMC network: they are the jump hosts
        if lname in services:
            services[lname]["networks"] = {"default": {}, "ipmi": {"ipv4_address": ip}}


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
    db["volumes"] = ["./secrets/slurmdbd:/run/workbench/etc:ro"]
    # slurmdbd insists its config is owned by SlurmUser and mode 0600, so install a copy at start.
    install = ("cp /run/workbench/etc/* /etc/slurm/ && chown slurm:slurm /etc/slurm/slurmdbd.conf"
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
        # The mocks and the GPU exporter read the node list from this copy, so they agree with slurm.conf even when the
        # workbench or `scale` changed the counts (the repository's sim.yaml is the default, not the running truth).
        shown = {**sim, "compute": {**sim.get("compute", {}), **({"count": compute_nodes} if compute_nodes is not None else {})}}
        (out.parent / "sim.yaml").write_text(yaml.safe_dump(shown, sort_keys=False))
        (out.parent / "nhc").mkdir(exist_ok=True)  # mounted by every node; filled only with checks that validate (ADR 0019)
        if errs := nhc.sync(out=out.parent / "nhc"):
            print(f"WARNING: health checks not delivered, the nodes keep the last good set (benchctl validate checks):\n{errs[0]}", file=sys.stderr)
    out.write_text("# GENERATED from sim.yaml by `benchctl compose`; do not edit.\n"
                   + yaml.safe_dump(compose, sort_keys=False))
    return out
