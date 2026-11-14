"""One addressing plan: every container has a fixed private IP, so a client on this host uses real addresses at the
standard ports instead of 127.0.0.1 (ADR 0018, issues 147-149).

The Docker bridge subnets are routed on the host, so no port publishing is needed to reach them. Two /16 networks follow
Bright Cluster Manager's habit: `internalnet` (10.141.0.0/16) for nodes and services, `ipminet` (10.148.0.0/16) for BMCs.
Compute nodes get 10.141.<i // 254>.<i % 254 + 1> and GPU nodes 10.141.<100 + i // 254>.<i % 254 + 1> (i counts within
the kind, so scaling one kind never renumbers the other); the head node (login) is 10.141.255.254, further login nodes (login2, ...) are .252, .251, ... and the services live in
10.141.255.0/24. components/bcm-mock/bcm.py repeats the node formula (it is stdlib-only and cannot
import this file); tests/test_addressing.py keeps the two equal."""
import ipaddress

from . import dcgm
from . import logins as logins_mod
from .nodes import hardware_nodes

INTERNAL = "10.141.0.0/16"
IPMI = "10.148.0.0/16"
# Docker gives a network's gateway the first address (x.x.0.1) unless told otherwise, which would be the first node's.
GATEWAY = "10.141.255.253"
IPMI_GATEWAY = "10.148.255.253"
LOGIN_IP = "10.141.255.254"
IPMI_LOGIN_IP = "10.148.255.254"  # the head node is also on the BMC network (the jump host)

IPMI_FARM_IP = "10.148.255.12"    # the farm that owns every BMC's state
BMC_NODE_PORT_BASE = 9000          # the farm's per-node TLS port: 9000 + index in node order (components/bmc-farm/bmc.py)
# service -> last octet in 10.141.255.0/24 (13 to 15 are reserved, unused: the scenario engine, user sim and ticket bot are not containers, ADR 0006). observability.yml (a static file) repeats the three observability ones.
DCGM_BASE = 50  # the real DCGM exporters (benchctl/dcgm.py) take 50, 51, ... in node order, one per node (up to 8), named by dcgm.service; planned only when the part is on (issue 434)
SERVICE_HOSTS = {"slurmctld": 1, "slurmrestd": 2, "slurmdbd": 3, "mariadb": 4, "jump": 5, "bcm-mock": 10, "jira-mock": 11,
                 "bmc-farm": 12, "jira-real": 16, "jira-db": 17,
                 "teleport": 30, "objectstore": 31, "k8s-apiserver": 40, "k8s-etcd": 41, "k8s-controller-manager": 42, "k8s-scheduler": 43,
                 "kwok-controller": 44, "prometheus": 20, "grafana": 21, "gpu-exporter-farm": 22, "alertmanager": 23,
                 "real-gpu": 24}  # the real card's exporter (benchctl/real_gpu.py), planned always, used when that part is on


class AddressingError(Exception):
    pass


def login_ip(index: int, subnet: str = INTERNAL) -> str:
    """The first login node keeps .255.254; the next ones count down from .252 (.253 is the network gateway), clear of
    the service block (hosts 1 to 30)."""
    a, b = _prefix(subnet)
    return f"{a}.{b}.255.{254 if index == 0 else 253 - index}"


def service_ip(name: str) -> str:
    """The fixed address of a service (10.141.255.x)."""
    a, b = _prefix(INTERNAL)
    return f"{a}.{b}.255.{SERVICE_HOSTS[name]}"


def _prefix(subnet: str) -> tuple[int, int]:
    return tuple(int(x) for x in subnet.split("/")[0].split(".")[:2])  # a /16: the first two octets


REAL_GPU_IP = "10.141.254.1"  # the real card's node (benchctl/real_gpu.py): third octet 254 is unused by every block below
GPU_BLOCK = 100  # the third octet GPU nodes start at; compute nodes use 0..99 (25,400 of them)
BLOCK = {"compute": 0, "gpu": GPU_BLOCK, "k8s-cpu": 200, "k8s-gpu": 230}  # first third octet of each kind (Kubernetes nodes: BMC and BCM addresses)
ROOM = {"compute": 100, "gpu": 100, "k8s-cpu": 30, "k8s-gpu": 24}         # how many third octets each kind may use


def host_ip(subnet: str, index: int, kind: str = "compute") -> str:
    """The `index`-th (0-based) address of a kind of node in a /16 (never .0, never the 10.x.255.* service block)."""
    if not 0 <= index < 254 * ROOM[kind]:
        raise AddressingError(f"too many {kind} nodes for {subnet}")
    a, b = _prefix(subnet)
    return f"{a}.{b}.{index // 254 + BLOCK[kind]}.{index % 254 + 1}"


def dcgm_hosts(sim: dict) -> dict[str, int]:
    """{service: last octet} of the real DCGM exporters this simulation has (none without a GPU node)."""
    return {dcgm.service(sim, n): DCGM_BASE + i for i, n in enumerate(dcgm.nodes(sim))}


def plan(sim: dict, compute_override: int | None = None) -> dict:
    """{"internal", "ipmi", "login", "nodes": {name: ip}, "bmc": {node: ip}, "services": {name: ip}}."""
    a, b = _prefix(INTERNAL)
    seen = dict.fromkeys(BLOCK, 0)
    nodes, bmc = {}, {}
    for name, kind in hardware_nodes(sim, compute_override):
        if kind in ("compute", "gpu"):  # Slurm nodes only: Teleport and ssh use `nodes`; a Kubernetes node has a BMC address and no SSH
            nodes[name] = host_ip(INTERNAL, seen[kind], kind)
        bmc[name] = host_ip(IPMI, seen[kind], kind)
        seen[kind] += 1
    if (sim.get("real_gpu") or {}).get("enabled"):
        nodes[sim["real_gpu"]["node"]] = REAL_GPU_IP
    logins = {logins_mod.name(i): login_ip(i) for i, _ in enumerate(logins_mod.modes(sim))}
    return {"internal": INTERNAL, "ipmi": IPMI, "login": LOGIN_IP, "logins": logins, "ipmi_logins": {n: login_ip(i, IPMI) for i, n in enumerate(logins)}, "nodes": nodes, "bmc": bmc,
            "bmc_port": {n: BMC_NODE_PORT_BASE + i for i, (n, _) in enumerate(hardware_nodes(sim, compute_override))},
            "services": {s: f"{a}.{b}.255.{h}" for s, h in {**SERVICE_HOSTS, **dcgm_hosts(sim)}.items()}}


def address_of(p: dict, name: str) -> str | None:
    """The internalnet IP of a node or service (the login node included), or None."""
    return p["logins"].get(name) or p["nodes"].get(name) or p["services"].get(name)


def overlaps(routes_text: str, subnets: tuple[str, ...] = (INTERNAL, IPMI)) -> list[str]:
    """Host routes (`ip route` text) that overlap a planned subnet. Docker's own bridge routes (dev br-*, docker0) are
    ours or another stack's and are not a conflict here; a VPN or office route is."""
    wanted = [ipaddress.ip_network(s) for s in subnets]
    out = []
    for line in routes_text.splitlines():
        words = line.split()
        if not words or words[0] == "default" or "/" not in words[0] and not words[0][0].isdigit():
            continue
        dev = words[words.index("dev") + 1] if "dev" in words and words.index("dev") + 1 < len(words) else ""
        if dev.startswith(("br-", "docker")):
            continue
        try:
            net = ipaddress.ip_network(words[0], strict=False)
        except ValueError:
            continue
        out += [f"{net} (dev {dev or '?'}) overlaps {w}" for w in wanted if net.overlaps(w)]
    return out
