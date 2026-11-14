"""What each component really uses, so the plan can admit against use and not against caps (issue 287).

A container's `mem_limit` is a cap the governor and Docker enforce; it is generous on purpose. Admitting against the caps
refused starts that fit (5.1 to 5.8 GB planned for a selection that used 3.0 GiB). Here every component has the largest
working set seen in a live run (`MEASURED_MB`, docker stats, the date in `MEASURED_ON`), the plan expects that plus
`TOLERANCE`, never more than the cap, and a component nobody has measured is expected at its cap (conservative).
The app under test is a separate line: `APP_MB`, left out with WORKBENCH_APP_MB=0 or `--app-mb 0` when it already runs
(its memory is then already out of MemAvailable)."""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

TOLERANCE = 0.15
APP_MB = 1024  # measured once: 664 MB peak for a desktop client under the client-stress scenario (docs/guides/memory.md); rounded up
APP_ENV = "WORKBENCH_APP_MB"
APP_FILE = Path(__file__).resolve().parent.parent / "compose" / "generated" / "app_reserve.json"  # gitignored; `benchctl measure app --save`
MEASURED_ON = "2026-11-05"
MEASURED_SETUP = "2 cpu + 2 gpu nodes (4 GPUs each), accounting, bmc, bcm, jira, teleport, scenarios, load on; real Jira on its own"
# key -> largest working set in MiB. "slurmd" is every compute node, "bmc-forwarder" every per-node BMC address.
MEASURED_MB: dict[str, int] = {
    "slurmd": 13, "bmc-forwarder": 18, "bmc-farm": 14, "bcm-mock": 12, "jira-mock": 20, "login": 10, "jump": 2,  # jump host: 1.9 MB peak after five forwarded sessions (issue 415)
    "mariadb": 204, "slurmdbd": 7, "slurmctld": 41, "slurmrestd": 7, "teleport": 222,
    "prometheus": 22, "grafana": 54, "gpu-exporter-farm": 19,
    "alertmanager": 20,  # estimate from the upstream footprint, not yet measured here (issue 460): run `benchctl measure` and replace
    # Kubernetes (ADR 0034): 4 KWOK nodes and the fixtures, one run (issue 232); etcd sat at its first 128 MB cap, so 130 is a floor
    "k8s-etcd": 130, "k8s-apiserver": 201, "k8s-controller-manager": 47, "k8s-scheduler": 18, "kwok-controller": 11,
    "real-gpu": 481,  # the real driver: host engine 404 MB + exporter 77 MB, one run on a laptop RTX card (issue 428)
    "dcgm-exporter": 125,  # exporter 97 MB + host engine 25 MB with 8 GPUs, one run (issue 426); the embedded engine was 430 MB
    "jira-db": 130, "jira-real": 2045,   # real Jira Data Center: heap 768 MB, JVM_ARGS (Serial GC, C1 only); 2176 MB cap, two clean runs
}
# What each part that is multiplied by the node count may cost per node, in MB (the declared cost; issue 456). A new per-node part
# must be listed here (tests/test_memory.py finds it by generating two sizes), and its cap and measurement must fit under it. A
# part that cannot be a few MB per node is shared (one container for every node) or optional.
PER_NODE_MB: dict[str, int] = {
    "slurmd": 60,          # every compute node; 13 MB measured
    "bmc-forwarder": 32,   # every per-node BMC address; 18 MB measured
    "dcgm-exporter": 150,  # optional (--with dcgm), real-gpu nodes only; 125 MB measured, the one part that is not a few MB
}
NOT_MEASURED = ("accounting-init",)  # one-shot: expected at the cap


def key_of(name: str, labels: dict | None = None) -> str:
    """The table key of a compose service name."""
    if labels and labels.get("workbench.node"):
        return "slurmd"
    if name.startswith("bmc-") and name != "bmc-farm":
        return "bmc-forwarder"
    if name.startswith("dcgm-exporter-"):  # one exporter per node (issue 435)
        return "dcgm-exporter"
    return name


def expected_mb(name: str, limit_mb: int, labels: dict | None = None, tolerance: float = TOLERANCE) -> int:
    """What the plan expects the component to use: measured use plus the tolerance, never above its cap."""
    seen = MEASURED_MB.get(key_of(name, labels))
    return limit_mb if seen is None else min(limit_mb, math.ceil(seen * (1 + tolerance)))


OVERHEAD_FILE = Path(__file__).resolve().parent.parent / "compose" / "generated" / "overhead.json"  # gitignored; `benchctl measure overhead --save`


def saved_overhead() -> dict | None:
    """What `benchctl measure overhead --save` wrote: {"per_container_mb": N, "fixed_mb": N, "measured": "YYYY-MM-DD"}, else None."""
    try:
        data = json.loads(OVERHEAD_FILE.read_text())
        return {"per_container_mb": int(data["per_container_mb"]), "fixed_mb": int(data.get("fixed_mb", 0)), "measured": str(data.get("measured", "?"))}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_overhead(per_container_mb: int, fixed_mb: int) -> Path:
    OVERHEAD_FILE.parent.mkdir(parents=True, exist_ok=True)
    OVERHEAD_FILE.write_text(json.dumps({"per_container_mb": per_container_mb, "fixed_mb": fixed_mb, "measured": time.strftime("%Y-%m-%d")}) + "\n")
    return OVERHEAD_FILE


def overhead_mb(new_containers: int) -> tuple[int, str]:
    """MB Docker's own processes add for containers a start would create (one containerd-shim each; `docker stats` does not count
    them), and where the figure comes from. Not measured on this machine: 0, and the note says so (never a guessed number)."""
    saved = saved_overhead()
    if not saved:
        return 0, "not measured: run `benchctl measure overhead --save` with the stack up"
    return saved["per_container_mb"] * max(0, new_containers), f"measured on {saved['measured']}: {saved['per_container_mb']} MB per container"


def saved_app() -> dict | None:
    """The reserve `benchctl measure app --save` wrote on this machine ({"mb": N, "measured": "YYYY-MM-DD"}), else None."""
    try:
        data = json.loads(APP_FILE.read_text())
        return {"mb": int(data["mb"]), "measured": str(data.get("measured", "?"))} if int(data["mb"]) >= 0 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_app(mb: int) -> Path:
    APP_FILE.parent.mkdir(parents=True, exist_ok=True)
    APP_FILE.write_text(json.dumps({"mb": mb, "measured": time.strftime("%Y-%m-%d")}) + "\n")
    return APP_FILE


def app_reserve() -> tuple[int, str]:
    """MB reserved for the app under test and where the number comes from.
    Precedence: --app-mb (it sets the env), WORKBENCH_APP_MB, the saved measurement, then APP_MB; 0 leaves it out."""
    raw = os.environ.get(APP_ENV, "")
    if raw.strip().isdigit():
        return int(raw), "given"
    saved = saved_app()
    if saved:
        return saved["mb"], f"measured on {saved['measured']}"
    return APP_MB, "assumed, not measured"


def app_mb() -> int:
    """MB reserved for the app under test (see app_reserve); 0 leaves it out."""
    return app_reserve()[0]
