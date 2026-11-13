"""Stack keys and module order for the live suite (issue 576, V-3). Pure, no pytest or Docker import, so a unit test can load it.

A running stack has a key: the sim, the profile or selection, the test timers. A module that is isolated only because of its
configuration (`STACK_REUSE = True`) reuses a running stack with the same key instead of a cold start; a different key means
down, then up (a different slurm.conf or selection must never meet a running one, issue 535)."""
import hashlib
import json
import os


def stack_key(sim: dict, profile: str, prof: dict | None = None, env: dict | None = None) -> str:
    env = os.environ if env is None else env
    blob = json.dumps({"sim": sim, "profile": profile, "prof": prof, "timers": env.get("WORKBENCH_TEST_TIMERS", "")}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def order_key(module: str, isolated: set[str], reuse: bool, group: str = "") -> tuple[int, str]:
    """Shared modules first, then STACK_REUSE modules next to the ones with the same group, then the cold-start modules."""
    if module not in isolated:
        return (0, "")
    return (1, group) if reuse else (2, "")
