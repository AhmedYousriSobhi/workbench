"""Log trace for the resilience scenarios (#220): after every step, what the jobs and the Slurm daemons did.

`benchctl scenario run NAME --trace` prints, per step, the job states and `slurmstepd` pids before and after and the
new lines `slurmctld`, `slurmd` and `slurmstepd` wrote, so a reader sees what happened and why. Pure functions over an
injected runner, so they unit-test without Docker.
"""
import re
import time

from . import checks
from .scenario import LOGIN, SLURMCTLD, Outcome, Step

NOISE = re.compile(r"munge|healthcheck|nhc", re.IGNORECASE)
MAX_LINES = 12  # per container per step; the rest is counted, not printed


def container(node: str) -> str:
    return f"workbench-{node}-1"


def nodes_of(sc: dict) -> list[str]:
    """Nodes the scenario touches, in first-use order (the submit node counts)."""
    seen: list[str] = []
    for s in sc.get("steps", []):
        do = s.get("do") or {}
        names = [n for key in ("slurmd_stop", "slurmd_start", "slurmd_restart", "drain", "resume", "down") for n in do.get(key) or []]
        if isinstance(do.get("submit"), dict):
            names.append(do["submit"]["node"])
        seen += [n for n in names if n not in seen]
    return seen


def source(line: str) -> str:
    return "slurmstepd" if "slurmstepd" in line else "slurmctld" if "slurmctld" in line else "slurmd"


def jobs(runner=checks.run) -> list[str]:
    """`id state node` of every job, or a note when slurmctld does not answer (which is itself the story)."""
    r = runner(["docker", "exec", LOGIN, "squeue", "-h", "-o", "%i %T %N"])
    if r.returncode:
        return ["squeue: no answer from slurmctld"]
    return [" ".join(line.split()) for line in r.stdout.splitlines()] or ["(no jobs)"]


def stepd_pids(node: str, runner=checks.run) -> str:
    r = runner(["docker", "exec", container(node), "pgrep", "-x", "slurmstepd"])
    return ",".join(r.stdout.split()) if r.returncode == 0 and r.stdout.strip() else "none"


def slurmd_pids(node: str, runner=checks.run) -> str:
    r = runner(["docker", "exec", container(node), "pgrep", "-x", "slurmd"])
    return ",".join(r.stdout.split()) if r.returncode == 0 and r.stdout.strip() else "none"


def log_lines(name: str, since: float, runner=checks.run) -> list[str]:
    r = runner(["docker", "logs", "--since", f"{since:.3f}", name])
    return [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip() and not NOISE.search(ln)]


class Tracer:
    """Prints a timeline; `step` is the engine's on_step hook. Everything it says goes through `say`."""

    def __init__(self, nodes: list[str], say=print, runner=checks.run, clock=time.time):
        self.nodes, self.say, self.runner, self.clock = nodes, say, runner, clock
        self.mark = clock()
        self.before = self.state()

    def state(self) -> str:
        parts = ["jobs: " + "; ".join(jobs(self.runner))]
        parts += [f"{n}: slurmd={slurmd_pids(n, self.runner)} slurmstepd={stepd_pids(n, self.runner)}" for n in self.nodes]
        return "\n    ".join(parts)

    def step(self, step: Step, outcome: Outcome) -> None:
        since, self.mark = self.mark, self.clock()
        self.say(f"  before: {self.before}")
        for name in [SLURMCTLD] + [container(n) for n in self.nodes]:
            lines = log_lines(name, since, self.runner)
            for ln in lines[:MAX_LINES]:
                self.say(f"  log {source(ln):10} {name.removeprefix('workbench-').removesuffix('-1'):10} {ln.strip()}")
            if len(lines) > MAX_LINES:
                self.say(f"  log ... {len(lines) - MAX_LINES} more lines in `docker logs {name}`")
        self.before = self.state()
        self.say(f"  after:  {self.before}")
