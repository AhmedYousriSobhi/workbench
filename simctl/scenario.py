"""Scenario engine (SPEC C-9): run scenarios/*.yaml against the live stack.

A scenario is `steps` (`at`, `target`, `do`) and optional `expect` assertions. Each step is one HTTP call to a
mock's /sim/inject (or a `scontrol` call in slurmctld). `ramp` steps expand into a series of temperature steps.
Targets whose component is not built yet (k8s, ticket-bot) are reported as skipped, not failed.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import checks
from .nodes import node_names

ENDPOINTS = {"gpu-exporter": "http://127.0.0.1:9400/sim/inject", "bmc-farm": "http://127.0.0.1:8000/sim/inject",
             "bcm-mock": "http://127.0.0.1:8081/sim/inject", "jira": "http://127.0.0.1:8080/sim/failure"}
SLURMCTLD = "hpcsim-slurmctld-1"
NOT_BUILT = ("k8s", "ticket-bot")
RAMP_STEP_S = 10.0
RAMP_START_C = 50.0
SCENARIOS = Path("scenarios")


class ScenarioError(ValueError):
    pass


def parse_duration(text) -> float:
    """'90s' / '2m' / '1h' / 45 -> seconds."""
    if isinstance(text, (int, float)):
        return float(text)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(s|m|h)?\s*", str(text))
    if not m:
        raise ScenarioError(f"bad duration {text!r}")
    return float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, None: 1}[m.group(2)]


@dataclass
class Step:
    at: float
    target: str
    do: dict


@dataclass
class Outcome:
    label: str
    status: str  # ok | failed | skipped
    detail: str = ""


@dataclass
class Report:
    name: str
    steps: list[Outcome] = field(default_factory=list)
    expects: list[Outcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(o.status != "failed" for o in self.steps + self.expects)


def load(name: str, root: Path = SCENARIOS) -> dict:
    path = root / f"{name}.yaml"
    if not path.is_file():
        raise ScenarioError(f"no scenario {name!r} (see: simctl scenario list)")
    return yaml.safe_load(path.read_text())


def list_all(root: Path = SCENARIOS) -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted(root.glob("*.yaml"))]


def plan(sc: dict) -> list[Step]:
    """Steps in time order; a `ramp: {to, over}` step becomes temperature steps every RAMP_STEP_S."""
    out: list[Step] = []
    for s in sc.get("steps", []):
        at, target, do = parse_duration(s["at"]), s["target"], dict(s.get("do") or {})
        ramp = do.pop("ramp", None)
        if ramp:
            over, goal = parse_duration(ramp["over"]), float(str(ramp["to"]).rstrip("C"))
            n = max(1, int(over // RAMP_STEP_S))
            for i in range(1, n + 1):
                out.append(Step(at + over * i / n, target, {**do, "temp": round(RAMP_START_C + (goal - RAMP_START_C) * i / n, 1)}))
        if not ramp or set(do) - {"nodes"}:  # a ramp's own step carries nothing but the node list
            out.append(Step(at, target, do))
    return sorted(out, key=lambda s: s.at)


def validate(sc: dict, sim: dict) -> list[str]:
    """Problems found without touching the stack: unknown targets, nodes or durations."""
    problems = []
    nodes = {n for n, _ in node_names(sim)}
    for i, s in enumerate(sc.get("steps", []), 1):
        try:
            parse_duration(s["at"])
        except (KeyError, ScenarioError):
            problems.append(f"step {i}: bad or missing 'at'")
        t = s.get("target")
        if t not in ENDPOINTS and t != "slurm" and t not in NOT_BUILT:
            problems.append(f"step {i}: unknown target {t!r}")
        do = s.get("do") or {}
        names = (do.get("nodes") or []) + (do.get("drain") or []) + (do.get("resume") or [])
        problems += [f"step {i}: unknown node {n!r}" for n in names if n not in nodes]
    return problems


def post(url: str, body: dict, timeout: float = 10) -> None:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    urllib.request.urlopen(req, timeout=timeout).close()


def execute(step: Step, poster=post, runner=checks.run) -> Outcome:
    """Apply one step; the label names what was done, status says whether it worked."""
    label = f"{step.target} {json.dumps(step.do, sort_keys=True)}"
    if step.target in NOT_BUILT:
        return Outcome(label, "skipped", f"{step.target} is not built yet")
    try:
        if step.target == "slurm":
            for n in step.do.get("drain", []):
                r = runner(["docker", "exec", SLURMCTLD, "scontrol", "update", f"nodename={n}", "state=drain",
                            f"reason={step.do.get('reason', 'scenario')}"])
                if r.returncode:
                    return Outcome(label, "failed", (r.stderr or r.stdout).strip())
            for n in step.do.get("resume", []):
                r = runner(["docker", "exec", SLURMCTLD, "scontrol", "update", f"nodename={n}", "state=resume"])
                if r.returncode:
                    return Outcome(label, "failed", (r.stderr or r.stdout).strip())
        elif step.target == "jira":
            poster(ENDPOINTS["jira"], step.do.get("failure", {"mode": None}))
        else:
            for n in step.do.get("nodes", []):
                poster(ENDPOINTS[step.target], {"node": n, **{k: v for k, v in step.do.items() if k != "nodes"}})
    except urllib.error.HTTPError as e:
        return Outcome(label, "failed", f"{step.target} answered {e.code}")
    except (OSError, KeyError) as e:
        return Outcome(label, "failed", f"{step.target} unreachable or bad step: {e}")
    return Outcome(label, "ok")


def node_state(node: str, runner=checks.run) -> str:
    r = runner(["docker", "exec", SLURMCTLD, "sinfo", "-h", "-N", "-n", node, "-o", "%T"])
    return r.stdout.strip().split("\n")[0] if r.returncode == 0 else ""


def jira_labels(labels: list[str]) -> bool:
    jql = "labels = " + " AND labels = ".join(labels)
    url = "http://127.0.0.1:8080/rest/api/2/search?jql=" + urllib.parse.quote(jql)
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())["total"] > 0
    except (OSError, ValueError):
        return False


def expectation(exp: dict, runner=checks.run):
    """(label, holds(), within seconds) for one assertion; holds is None when it cannot be evaluated here."""
    (kind, args), = exp.items()
    label = f"{kind} {json.dumps({k: v for k, v in args.items() if k != 'within'}, sort_keys=True)}"
    within = parse_duration(args.get("within", "0s"))
    if kind == "slurm.node_state":
        return label, lambda: args["state"] in node_state(args["node"], runner), within
    if kind == "jira.issue_created":
        return label, lambda: jira_labels(args["labels"]), within
    return label, None, within


def check_expect(exp: dict, factor: float, sleep=time.sleep, clock=time.monotonic, runner=checks.run) -> Outcome:
    """Poll one assertion until it holds or `within` (scaled by the speed factor) runs out."""
    label, holds, within = expectation(exp, runner)
    if holds is None:
        return Outcome(label, "skipped", "cannot be evaluated here")
    deadline = clock() + within / factor
    while not holds():
        if clock() >= deadline:
            return Outcome(label, "failed", "did not hold within the time limit")
        sleep(min(1.0, max(0.0, deadline - clock())))
    return Outcome(label, "ok")


def run(sc: dict, speed: float = 1.0, dilation: float = 1.0, say=print, sleep=time.sleep, clock=time.monotonic,
        poster=post, runner=checks.run) -> Report:
    """Play the scenario: wait for each step's time (divided by speed x time_dilation) and apply it.

    Every expectation is watched from the start: it passes the moment it holds (even if a later step undoes it) and
    fails if its `within` (from the start) runs out first.
    """
    factor = max(speed * dilation, 1e-6)
    report = Report(sc["name"])
    start = clock()
    pending = []
    for exp in sc.get("expect") or []:
        label, holds, within = expectation(exp, runner)
        if holds is None:
            report.expects.append(Outcome(label, "skipped", "cannot be evaluated here"))
        else:
            pending.append((label, holds, start + within / factor))

    def watch() -> None:
        for item in list(pending):
            label, holds, deadline = item
            if holds():
                outcome = Outcome(label, "ok")
            elif clock() >= deadline:
                outcome = Outcome(label, "failed", "did not hold within the time limit")
            else:
                continue
            pending.remove(item)
            report.expects.append(outcome)
            say(f"expect  {outcome.status:7} {outcome.label}" + (f"  ({outcome.detail})" if outcome.detail else ""))

    def wait_until(t: float) -> None:
        while (left := t - clock()) > 0:
            sleep(min(left, 1.0) if pending else left)
            watch()

    for step in plan(sc):
        wait_until(start + step.at / factor)
        outcome = execute(step, poster, runner)
        report.steps.append(outcome)
        say(f"[{step.at:6.1f}s] {outcome.status:7} {outcome.label}" + (f"  ({outcome.detail})" if outcome.detail else ""))
        watch()
    while pending:
        sleep(1.0)
        watch()
    return report
