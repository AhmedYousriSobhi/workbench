"""What each component really uses, so the plan can admit against use and not against caps (issue 287).

A container's `mem_limit` is a cap the governor and Docker enforce; it is generous on purpose. Admitting against the caps
refused starts that fit (5.1 to 5.8 GB planned for a selection that used 3.0 GiB). Here every component has the largest
working set seen in a live run (`MEASURED_MB`, docker stats, the date in `MEASURED_ON`), the plan expects that plus
`TOLERANCE`, never more than the cap, and a component nobody has measured is expected at its cap (conservative).
The app under test is a separate line: `APP_MB`, left out with WORKBENCH_APP_MB=0 or `--app-mb 0` when it already runs
(its memory is then already out of MemAvailable)."""
from __future__ import annotations

import math
import os

TOLERANCE = 0.15
APP_MB = 4096
APP_ENV = "WORKBENCH_APP_MB"
MEASURED_ON = "2026-11-05"
MEASURED_SETUP = "2 cpu + 2 gpu nodes (4 GPUs each), accounting, bmc, bcm, jira, teleport, scenarios, load on; real Jira on its own"
# key -> largest working set in MiB. "slurmd" is every compute node, "bmc-forwarder" every per-node BMC address.
MEASURED_MB: dict[str, int] = {
    "slurmd": 13, "bmc-forwarder": 18, "bmc-farm": 14, "bcm-mock": 12, "jira-mock": 20, "login": 10,
    "mariadb": 204, "slurmdbd": 7, "slurmctld": 41, "slurmrestd": 7, "teleport": 222,
    "prometheus": 22, "grafana": 54, "gpu-exporter-farm": 19,
    "jira-db": 130, "jira-real": 2363,   # real Jira Data Center: heap 768 MB, 2560 MB cap; at 2304 MB it was OOM-killed once
}
NOT_MEASURED = ("scenario-engine", "user-sim", "ticket-bot", "accounting-init")  # not built or one-shot: expected at the cap


def key_of(name: str, labels: dict | None = None) -> str:
    """The table key of a compose service name."""
    if labels and labels.get("workbench.node"):
        return "slurmd"
    if name.startswith("bmc-") and name != "bmc-farm":
        return "bmc-forwarder"
    return name


def expected_mb(name: str, limit_mb: int, labels: dict | None = None, tolerance: float = TOLERANCE) -> int:
    """What the plan expects the component to use: measured use plus the tolerance, never above its cap."""
    seen = MEASURED_MB.get(key_of(name, labels))
    return limit_mb if seen is None else min(limit_mb, math.ceil(seen * (1 + tolerance)))


def app_mb() -> int:
    """MB reserved for the app under test: WORKBENCH_APP_MB, else APP_MB; 0 leaves it out."""
    raw = os.environ.get(APP_ENV, "")
    return int(raw) if raw.strip().isdigit() else APP_MB
