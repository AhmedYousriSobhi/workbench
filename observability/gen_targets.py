"""Generate Prometheus file_sd targets from the node inventory so node names agree everywhere (issues 462, 458).

`benchctl up` already writes these (benchctl/promtargets.py); this script writes the same file by hand for a look or a test.
Argument 1 is `sim.yaml` (default; the inventory is built from it, the same code `benchctl up` uses) or an `inventory.json`."""
import json
import sys
from pathlib import Path


def load_inventory(src: Path) -> dict:
    if src.suffix == ".json":
        return json.loads(src.read_text())
    import yaml

    from benchctl import hwinventory

    return hwinventory.build(yaml.safe_load(src.read_text()))


def targets(inventory: dict, exporter: str = "gpu-exporter-farm:9400") -> list[dict]:
    from benchctl import promtargets

    return promtargets.node_targets(inventory, exporter)


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "sim.yaml")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "compose/generated/prometheus/targets/sim-nodes.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(targets(load_inventory(src)), indent=2) + "\n")
