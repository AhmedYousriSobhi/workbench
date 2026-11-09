"""The real DCGM exporter, opt-in (the `dcgm` part, issue 426, ADR 0025).

One official dcgm-exporter on NVIDIA's mock NVML stands for the first GPU node: genuine DCGM series names, labels and formats with no
GPU and no driver. The hand-written exporter (`gpu-exporter-farm`) stays and stays the default for every node; it also drives this one:
it writes an override file the mock re-reads every second (Slurm allocations, injected XID and temperature), see
`components/gpu-exporter-farm/exporter.py`. About 430 MB, so it is off by default (priority P6)."""
from pathlib import Path

SERVICE = "dcgm-exporter"
PORT = 9402                    # published on 127.0.0.1; the farm keeps 9400
LIMIT_MB = 600                 # measured 430 MB RSS (one exporter serves 1 to 8 GPUs alike)
DIR = "dcgm"                   # under compose/generated: the override file the farm writes
COLLECT_MS = 3000              # how often the exporter reads DCGM (its default is 30 s)
# sim.yaml gpu.type -> the mock's profile; a type with no profile of its own (h200) uses the nearest and says so
PROFILES = {"a100": "a100", "h100": "h100", "h200": "h100", "l40s": "l40s"}
DEFAULT_PROFILE = "h100"


def profile(sim: dict) -> str:
    return PROFILES.get(str((sim.get("gpu") or {}).get("type", "")).lower(), DEFAULT_PROFILE)


def node(sim: dict) -> str | None:
    """The GPU node this exporter stands for (the first), or None when the cluster has no GPU node."""
    g = sim.get("gpu") or {}
    return f"{g['prefix']}001" if g.get("nodes") else None


def problems(sim: dict) -> list[str]:
    return [] if node(sim) else ["the dcgm part needs a GPU node (gpu.nodes in sim.yaml, or --gpu-nodes 1)"]


def wire(services: dict, sim: dict) -> None:
    """Environment, mounts and the published port for the exporter service; a no-op when the part is off."""
    svc = services.get(SERVICE)
    if svc is None:
        return
    svc.update({
        "hostname": node(sim) or "gpu001",  # DCGM's Hostname label is the machine's hostname
        "environment": {"MOCK_NVML_CONFIG": f"/opt/mocknvml/configs/mock-nvml-config-{profile(sim)}.yaml",
                        "MOCK_NVML_NUM_DEVICES": str((sim.get("gpu") or {}).get("gpus_per_node", 1)),
                        "MOCK_NVML_OVERRIDES": f"/{DIR}/overrides.yaml", "MOCK_NVML_OVERRIDES_TTL": "1s"},
        "volumes": [f"./{DIR}:/{DIR}:ro"],
        "command": ["-c", str(COLLECT_MS)],
        "ports": [f"127.0.0.1:{PORT}:9400"]})


def prepare(root: Path) -> None:
    """The directory the farm writes into, made before the containers start (it is mounted into the farm unconditionally)."""
    d = root / DIR
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o777)  # the farm runs as `nobody`; localhost-only, generated, holds no secret
    (d / "overrides.yaml").touch(exist_ok=True)
    (d / "overrides.yaml").chmod(0o666)
