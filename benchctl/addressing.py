"""One addressing plan: every container has a fixed private IP, so a client on this host uses real addresses at the
standard ports instead of 127.0.0.1 (ADR 0018, issues 147-149).

The Docker bridge subnets are routed on the host, so no port publishing is needed to reach them. Two /16 networks follow
Bright Cluster Manager's habit: `internalnet` (10.141.0.0/16) for nodes and services, `ipminet` (10.148.0.0/16) for BMCs.
Compute nodes get 10.141.<i // 254>.<i % 254 + 1> and GPU nodes 10.141.<100 + i // 254>.<i % 254 + 1> (i counts within
the kind, so scaling one kind never renumbers the other); the head node (login) is 10.141.255.254 and the services live in
10.141.255.0/24. components/bcm-mock/bcm.py repeats the node formula (it is stdlib-only and cannot
import this file); tests/test_addressing.py keeps the two equal."""
import ipaddress

from .nodes import node_names

INTERNAL = "10.141.0.0/16"
IPMI = "10.148.0.0/16"
# Docker gives a network's gateway the first address (x.x.0.1) unless told otherwise, which would be the first node's.
GATEWAY = "10.141.255.253"
IPMI_GATEWAY = "10.148.255.253"
LOGIN_IP = "10.141.255.254"
IPMI_LOGIN_IP = "10.148.255.254"  # the head node is also on the BMC network (the jump host)
IPMI_FARM_IP = "10.148.255.12"    # the farm that owns every BMC's state
BMC_NODE_PORT_BASE = 9000          # the farm's per-node TLS port: 9000 + index in node order (components/bmc-farm/bmc.py)
# service -> last octet in 10.141.255.0/24. observability.yml (a static file) repeats the three observability ones.
SERVICE_HOSTS = {"slurmctld": 1, "slurmrestd": 2, "slurmdbd": 3, "mariadb": 4, "bcm-mock": 10, "jira-mock": 11,
                 "bmc-farm": 12, "scenario-engine": 13, "user-sim": 14, "ticket-bot": 15,
                 "prometheus": 20, "grafana": 21, "gpu-exporter-farm": 22}


class AddressingError(Exception):
    pass


def _prefix(subnet: str) -> tuple[int, int]:
    return tuple(int(x) for x in subnet.split("/")[0].split(".")[:2])  # a /16: the first two octets


GPU_BLOCK = 100  # the third octet GPU nodes start at; compute nodes use 0..99 (25,400 of them)


def host_ip(subnet: str, index: int, kind: str = "compute") -> str:
    """The `index`-th (0-based) address of a kind of node in a /16 (never .0, never the 10.x.255.* service block)."""
    if not 0 <= index < 254 * (GPU_BLOCK if kind == "compute" else 100):
        raise AddressingError(f"too many {kind} nodes for {subnet}")
    a, b = _prefix(subnet)
    return f"{a}.{b}.{index // 254 + (GPU_BLOCK if kind == 'gpu' else 0)}.{index % 254 + 1}"


def plan(sim: dict, compute_override: int | None = None) -> dict:
    """{"internal", "ipmi", "login", "nodes": {name: ip}, "bmc": {node: ip}, "services": {name: ip}}."""
    a, b = _prefix(INTERNAL)
    seen = {"compute": 0, "gpu": 0}
    nodes, bmc = {}, {}
    for name, kind in node_names(sim, compute_override):
        nodes[name], bmc[name] = host_ip(INTERNAL, seen[kind], kind), host_ip(IPMI, seen[kind], kind)
        seen[kind] += 1
    return {"internal": INTERNAL, "ipmi": IPMI, "login": LOGIN_IP, "nodes": nodes, "bmc": bmc,
            "bmc_port": {n: BMC_NODE_PORT_BASE + i for i, (n, _) in enumerate(node_names(sim, compute_override))},
            "services": {s: f"{a}.{b}.255.{h}" for s, h in SERVICE_HOSTS.items()}}


def address_of(p: dict, name: str) -> str | None:
    """The internalnet IP of a node or service (the login node included), or None."""
    return LOGIN_IP if name == "login" else p["nodes"].get(name) or p["services"].get(name)


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
