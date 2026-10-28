"""Generate Prometheus file_sd targets from sim.yaml so node names agree everywhere."""
import json
import sys
from pathlib import Path

import yaml


def targets(cfg: dict, exporter: str = "gpu-exporter-farm:9400") -> list[dict]:
    names = []
    c = cfg.get("compute", {})
    names += [f"{c['prefix']}{i:03d}" for i in range(1, c.get("count", 0) + 1)]
    g = cfg.get("gpu", {})
    names += [f"{g['prefix']}{i:03d}" for i in range(1, g.get("nodes", 0) + 1)]
    return [{"targets": [exporter], "labels": {"job": "sim-nodes", "nodes": ",".join(names)}}]


if __name__ == "__main__":
    sim = Path(sys.argv[1] if len(sys.argv) > 1 else "sim.yaml")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "observability/targets/sim-nodes.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(targets(yaml.safe_load(sim.read_text())), indent=2) + "\n")
