"""The real DCGM exporter, opt-in (the `dcgm` part, issue 426, ADR 0025).

One official dcgm-exporter on NVIDIA's mock NVML stands for the first GPU node (`--dcgm-nodes N` gives the first N one each, issue 435): genuine DCGM series names, labels and formats with no
GPU and no driver. The hand-written exporter (`gpu-exporter-farm`) stays and stays the default for every node; it also drives this one:
it writes an override file per node that the mock re-reads every second (Slurm allocations, injected XID and temperature), see
`components/gpu-exporter-farm/exporter.py`. About 125 MB (exporter plus host engine), off by default (priority P6)."""
import copy
from pathlib import Path

SERVICE = "dcgm-exporter"
PORT = 9402                    # the first node's exporter, published on 127.0.0.1; the farm keeps 9400, the real-gpu part 9403
EXTRA_PORT = 9410              # the exporter of the second node; the third is 9411, and so on
MAX_NODES = 8
LIMIT_MB = 256                 # measured 125 MB RSS with 8 GPUs: the exporter reading from a host engine in the same container
DIR = "dcgm"                   # under compose/generated: the override file the farm writes
COLLECT_MS = 3000              # how often the exporter reads DCGM (its default is 30 s)
# sim.yaml gpu.type -> the mock's profile; a type with no profile of its own (h200) uses the nearest and says so
PROFILES = {"a100": "a100", "h100": "h100", "h200": "h100", "l40s": "l40s"}
DEFAULT_PROFILE = "h100"
MODELS = {"h200": "NVIDIA H200"}  # a type whose profile is another card's: the model name the farm shows (the mock keeps the profile's own)


def profile(sim: dict) -> str:
    return PROFILES.get(str((sim.get("gpu") or {}).get("type", "")).lower(), DEFAULT_PROFILE)


def count(sim: dict) -> int:
    """How many GPU nodes get a real exporter: `dcgm.nodes` in sim.yaml (`--dcgm-nodes`), default 1, never more than the GPU nodes."""
    g = sim.get("gpu") or {}
    want = int((sim.get("dcgm") or {}).get("nodes") or 1)
    return min(max(want, 1), MAX_NODES, int(g.get("nodes") or 0))


def nodes(sim: dict) -> list[str]:
    """The GPU nodes that have a real exporter: the first `count`, none when the cluster has no GPU node."""
    prefix = (sim.get("gpu") or {}).get("prefix", "gpu")
    return [f"{prefix}{i:03d}" for i in range(1, count(sim) + 1)]


def node(sim: dict) -> str | None:
    """The first GPU node (the one `dcgm-exporter` stands for), or None when the cluster has no GPU node."""
    return (nodes(sim) or [None])[0]


def service(sim: dict, name: str) -> str | None:
    """The compose service of a node's exporter: `dcgm-exporter` for the first node, `dcgm-exporter-gpu002` for the next."""
    ns = nodes(sim)
    return None if name not in ns else SERVICE if name == ns[0] else f"{SERVICE}-{name}"


def port(sim: dict, name: str) -> int | None:
    ns = nodes(sim)
    return None if name not in ns else PORT if name == ns[0] else EXTRA_PORT + ns.index(name) - 1


def container(sim: dict, name: str) -> str | None:
    """The container of a node's exporter (compose project `workbench`), or None when that node has none."""
    return f"workbench-{svc}-1" if (svc := service(sim, name)) else None


def override_file(name: str) -> str:
    return f"{name}.yaml"


def problems(sim: dict) -> list[str]:
    out = [] if node(sim) else ["the dcgm part needs a GPU node (gpu.nodes in sim.yaml, or --gpu-nodes 1)"]
    want = int((sim.get("dcgm") or {}).get("nodes") or 1)
    if not 1 <= want <= MAX_NODES:
        out.append(f"--dcgm-nodes must be 1 to {MAX_NODES}, not {want}")
    elif node(sim) and want > count(sim):
        out.append(f"--dcgm-nodes {want} is more than the GPU nodes ({count(sim)}): add --gpu-nodes or lower it")
    return out


def wire(services: dict, sim: dict) -> None:
    """One exporter service per node (the first is in the catalog, the others are copies), each with its environment, mounts and published port;
    a no-op when the part is off."""
    base = services.get(SERVICE)
    if base is None or not node(sim):
        return
    for name in nodes(sim)[1:]:
        services[service(sim, name)] = {k: v for k, v in copy.deepcopy(base).items() if k != "build"}  # the image is built once, by the first
    model = MODELS.get(str((sim.get("gpu") or {}).get("type", "")).lower())
    for name in nodes(sim):
        env = {"MOCK_NVML_PROFILE": f"/opt/mocknvml/configs/mock-nvml-config-{profile(sim)}.yaml",  # start.sh keeps the first N devices
               "MOCK_NVML_GPUS": str((sim.get("gpu") or {}).get("gpus_per_node", 1)),
               "MOCK_NVML_OVERRIDES": f"/{DIR}/{override_file(name)}", "MOCK_NVML_OVERRIDES_TTL": "1s",
               "MOCK_NVML_NODE_INDEX": str(nodes(sim).index(name))}  # makes the GPU UUIDs differ from node to node
        if model:
            env["MOCK_NVML_NAME"] = model
        services[service(sim, name)].update({
            "environment": {**env, "DCGM_HOSTNAME": name},  # the Hostname label; not the container hostname, which Docker's DNS would resolve to this container instead of the node
            "environment_": None, "volumes": [f"./{DIR}:/{DIR}:ro"], "command": ["-c", str(COLLECT_MS)],
            "ports": [f"127.0.0.1:{port(sim, name)}:9400"]})


def prepare(root: Path, sim: dict | None = None) -> None:
    """The directory the farm writes into, made before the containers start (it is mounted into the farm unconditionally).
    On a later start the file belongs to the farm's user: leave it alone."""
    d = root / DIR
    d.mkdir(parents=True, exist_ok=True)
    files = [d / override_file(n) for n in nodes(sim or {})] or [d / override_file("gpu001")]
    for path, mode in [(d, 0o777), *((f, 0o666) for f in files)]:  # the farm runs as `nobody`; localhost-only, generated, holds no secret
        try:
            if path in files:
                path.touch(exist_ok=True)
            path.chmod(mode)
        except PermissionError:
            pass  # not ours (made by the farm's user or by Docker): already writable, or the farm reports it
