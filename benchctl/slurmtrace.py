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


def collect(nodes: list[str], since: float, runner=checks.run) -> dict:
    """Everything one trace entry needs, as plain data (the governor serves it to the page as JSON)."""
    return {"jobs": jobs(runner),
            "nodes": {n: {"slurmd": slurmd_pids(n, runner), "slurmstepd": stepd_pids(n, runner)} for n in nodes},
            "logs": {name: log_lines(name, since, runner) for name in [SLURMCTLD] + [container(n) for n in nodes]}}


def state_text(data: dict) -> str:
    parts = ["jobs: " + "; ".join(data["jobs"])]
    parts += [f"{n}: slurmd={v['slurmd']} slurmstepd={v['slurmstepd']}" for n, v in data["nodes"].items()]
    return "\n    ".join(parts)


def log_rows(data: dict) -> tuple[list[dict], dict]:
    """(rows `{source, where, line}` capped per container, {container: lines not shown})."""
    rows, more = [], {}
    for name, lines in data["logs"].items():
        where = name.removeprefix("workbench-").removesuffix("-1")
        rows += [{"source": source(ln), "where": where, "line": ln.strip()} for ln in lines[:MAX_LINES]]
        if len(lines) > MAX_LINES:
            more[name] = len(lines) - MAX_LINES
    return rows, more


def entry(label: str, before: dict | None, data: dict) -> dict:
    """One timeline entry: `label` (the step), the state before it, the new log lines, the state after."""
    rows, more = log_rows(data)
    return {"label": label, "before": state_text(before) if before else "", "after": state_text(data), "logs": rows, "more": more}


def render(e: dict) -> list[str]:
    out = [f"  before: {e['before']}"] if e["before"] else []
    out += [f"  log {r['source']:10} {r['where']:10} {r['line']}" for r in e["logs"]]
    out += [f"  log ... {n} more lines in `docker logs {name}`" for name, n in e["more"].items()]
    return out + [f"  after:  {e['after']}"]


class Tracer:
    """Prints a timeline from `collect(nodes, since)`; `step` is the engine's on_step hook (the CLI collects locally)."""

    def __init__(self, nodes: list[str], say=print, runner=checks.run, clock=time.time, collector=None):
        self.nodes, self.say, self.clock = nodes, say, clock
        self.collector = collector or (lambda n, since: collect(n, since, runner))
        self.mark = clock()
        self.before = self.collector(nodes, self.mark)

    def step(self, step: Step, outcome: Outcome) -> dict:
        since, self.mark = self.mark, self.clock()
        data = self.collector(self.nodes, since)
        e = entry(f"{step.target} {step.do}", self.before, data)
        for line in render(e):
            self.say(line)
        self.before = data
        return e


class Deferred:
    """The page's trace: step i's entry covers everything between running step i and running step i+1 (the governor does
    the step in the background, so asking right after it would show nothing yet). Wire `before_step` to the engine's
    on_before and call `finish` when the run ends; `collector(nodes, since)` may return None when nothing can be read."""

    def __init__(self, nodes: list[str], collector, on_entry, clock=time.time):
        self.nodes, self.collector, self.on_entry, self.clock = nodes, collector, on_entry, clock
        self.mark = clock()
        self.prev: tuple[str, dict] | None = None

    def _read(self) -> dict | None:
        t0 = self.clock()  # taken before reading: a line logged while reading is shown twice, never lost
        data, self.mark = self.collector(self.nodes, self.mark), t0
        if self.prev and data:
            self.on_entry(entry(self.prev[0], self.prev[1], data))
        return data

    def before_step(self, step: Step) -> None:
        data = self._read()
        self.prev = (f"{step.target} {step.do}", data) if data else None

    def finish(self) -> None:
        self._read()
        self.prev = None
