"""How the parts talk to each other (issue #396): the documented links, as data, for the parts one snapshot has.

A link is what the software's own documentation says it does (ports: slurm.conf man page, the Kubernetes "Ports and
protocols" page, Prometheus, DCGM exporter, Redfish and Teleport docs). Three facts stay apart and the page must not blur them:
- `real`: real software answers (Slurm, the Kubernetes API server, Teleport, Jira DC) or a mock imitates it.
- `measured`: a request counter backs it (`MEASURED`); every other link is configured wiring, not seen traffic.
- `plane`: control, data, monitoring, management or access.
"""

from .flow import COUNTERS, STATS

MEASURED = {*STATS, *COUNTERS}  # the services a request counter backs
COUNTED = {*MEASURED, "k8s-api"}  # plus the API server's own total (all callers); not an edge counter, so the architecture drawing ignores it

# (from, to, protocol, port, plane, what, needs, real, service the counter belongs to)
# `needs`: "slurm" always; a service name; "k8s-api"; "teleport"
LINKS = [
    (
        "login",
        "slurmctld",
        "TCP (Slurm RPC)",
        6817,
        "control",
        "squeue, sbatch, scontrol",
        "slurm",
        True,
        None,
    ),
    (
        "slurmd",
        "slurmctld",
        "TCP (Slurm RPC)",
        6817,
        "control",
        "node registration, heartbeat, job status",
        "slurm",
        True,
        None,
    ),
    (
        "slurmctld",
        "slurmd",
        "TCP (Slurm RPC)",
        6818,
        "control",
        "launch and signal jobs, health check",
        "slurm",
        True,
        None,
    ),
    ("clients", "slurmrestd", "HTTP + JWT", 6820, "access", "Slurm REST", "slurm", True, None),
    (
        "slurmrestd",
        "slurmctld",
        "TCP (Slurm RPC)",
        6817,
        "control",
        "REST calls become RPCs",
        "slurm",
        True,
        None,
    ),
    (
        "clients",
        "login",
        "SSH",
        22,
        "access",
        "key login (127.0.0.1:2222 fallback)",
        "slurm",
        True,
        None,
    ),
    ("login", "nodes", "SSH", 22, "access", "jump host to a node", "slurm", True, None),
    (
        "clients",
        "bmc-farm",
        "HTTPS (Redfish)",
        443,
        "management",
        "power, thermals, sensors, event log",
        "bmc-farm",
        False,
        "bmc-farm",
    ),
    (
        "clients",
        "bcm-mock",
        "HTTP (CMDaemon JSON)",
        8081,
        "management",
        "devices, categories, health checks",
        "bcm-mock",
        False,
        "bcm-mock",
    ),
    (
        "clients",
        "gpu-exporter",
        "HTTP (Prometheus text)",
        9400,
        "monitoring",
        "DCGM metrics scrape",
        "gpu-exporter",
        False,
        "gpu-exporter",
    ),
    (
        "clients",
        "jira-mock",
        "HTTP (REST v2)",
        8080,
        "management",
        "issues, JQL, webhooks",
        "jira-mock",
        False,
        "jira-mock",
    ),
    (
        "clients",
        "jira-real",
        "HTTP (REST)",
        8083,
        "management",
        "issues, JQL (Jira Data Center)",
        "jira-real",
        True,
        None,
    ),
    (
        "clients",
        "teleport",
        "HTTPS",
        3080,
        "access",
        "web UI, tsh login",
        "teleport",
        True,
        "teleport",
    ),
    (
        "teleport",
        "login",
        "SSH (agentless)",
        22,
        "access",
        "proxied session to a node",
        "teleport",
        True,
        None,
    ),
    ("clients", "k8s-api", "HTTPS", 6443, "control", "kubectl and every other caller (the count is the API server's total)", "k8s-api", True, "k8s-api"),
    (
        "kube-scheduler",
        "k8s-api",
        "HTTPS",
        6443,
        "control",
        "watch pods, bind them to nodes",
        "k8s-api",
        True,
        None,
    ),
    (
        "kube-controller-manager",
        "k8s-api",
        "HTTPS",
        6443,
        "control",
        "watch and reconcile objects",
        "k8s-api",
        True,
        None,
    ),
    (
        "k8s-api",
        "etcd",
        "gRPC (TLS)",
        2379,
        "data",
        "every object read and write",
        "k8s-api",
        True,
        None,
    ),
    (
        "kwok",
        "k8s-api",
        "HTTPS",
        6443,
        "control",
        "plays the kubelets: node and pod status",
        "k8s-api",
        False,
        None,
    ),
]


def build(services: dict, nodes: list, logins: list) -> list[dict]:
    """The documented links whose parts this snapshot has, each tagged real/simulated and measured/configured."""
    have = set(services)
    slurm = bool(logins) or any(n["kind"] != "k8s" for n in nodes)
    out = []
    for src, dst, proto, port, plane, what, needs, real, counter in LINKS:
        if needs == "slurm" and not slurm or needs != "slurm" and needs not in have:
            continue
        out.append(
            {
                "id": f"{src}>{dst}:{port}",
                "from": src,
                "to": dst,
                "protocol": proto,
                "port": port,
                "plane": plane,
                "what": what,
                "real": real,
                "measured": counter in COUNTED if counter else False,
                "service": counter,
            }
        )
    return out
