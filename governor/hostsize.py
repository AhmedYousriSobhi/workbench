"""Ladder defaults derived from the host section of sim.yaml (issue 464): one source of truth.

The governor runs on the system Python, which may lack PyYAML, so the two numbers are read by hand.
With the shipped sim.yaml (reserve 3 GB, floor 1 GB) this gives exactly the old README values:
L4 below 1.0 GB, L1 below 2.0 GB, calm above 2.5 GB. Host RAM itself is not used: `reserve_gb` already is the
owner's statement of how much the machine keeps for itself.
"""
import re
from pathlib import Path

from .signals import GB

DEFAULT = (3.0, 1.0)  # reserve_gb, safety_floor_gb: sim.yaml's values


def read_host(sim: Path) -> tuple[float, float]:
    """(reserve_gb, safety_floor_gb) from the `host:` block; the defaults when the file or a key is missing."""
    try:
        text = sim.read_text()
    except OSError:
        return DEFAULT
    m = re.search(r"^host:\s*\n((?:[ \t]+.*\n?)*)", text, re.MULTILINE)
    block = m.group(1) if m else ""
    got = dict(re.findall(r"^[ \t]+(reserve_gb|safety_floor_gb):[ \t]*([0-9.]+)", block, re.MULTILINE))
    return float(got.get("reserve_gb", DEFAULT[0])), float(got.get("safety_floor_gb", DEFAULT[1]))


def derive(reserve_gb: float, floor_gb: float) -> dict[str, int]:
    """l4 at the floor, l1 halfway to the reserve, calm a quarter of the span above l1."""
    span = max(reserve_gb - floor_gb, 0.0)
    return {"l4_mem": int(floor_gb * GB), "l1_mem": int((floor_gb + span / 2) * GB), "l0_mem": int((floor_gb + span * 3 / 4) * GB)}
