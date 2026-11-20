"""A scenario attached to a config lab run: `benchctl run lab NAME --scenario S` (issue 554, ADR 0050 and its addendum on scenarios).

The same faults at the same lab times on both sides of an A/B run, so the only difference between A and B stays the configuration:

- **Freeze.** `--prepare` resolves the scenario once and writes it read-only into the run folder (`scenario.json`, its sha256 in
  `run.json`); both sides and a restore-only child read that copy, never the live file. Node selectors (`first cpu node`) pick the
  run's own lab nodes, not sim.yaml's: `cpu` are the lab nodes without GPUs, `gpu` those with (the order of the downscale, so the
  pick is the same every time). A step that acts through Slurm (`drain`, `down`, `resume`) keeps the Slurm name (the production
  name, as the replay and the monitoring client see it); one that acts on the container (`power_off`, `power_on`, `slurmd_*`) gets
  the lab node's service name (`lab-node-NNN`, container `workbench-lab-node-NNN-1`).
- **What can play.** Only `slurm` steps on lab nodes and `slurmctld_restart`: the BMC farm, BCM, GPU exporter, DCGM, fault model and
  switch know only sim.yaml's nodes (the hardware inventory has no lab nodes), and `submit`, `cancel` and Slurm's own power save
  (`power_down`, `power_up`, `reboot`, through the BMC) need them too. Expectations: `slurm.node_state`, `slurm.job_state`,
  `slurm.stepd`. Anything else is refused at `--prepare` with one message per problem.
- **Time.** A scenario's `at:` is production time: it plays at `at / factor` seconds of the side's replay clock (`--speed` = the run's
  time factor, `time_dilation` not applied again), the clock of the jobs. Two steps on one node that land under a second apart only
  because of the factor are refused (the factor and the smallest production gap that fits are named), and so is a scenario that
  ends after the side.
- **Clock and pause.** The player's clock is the replay clock: it follows the driver's `replay_s` (`@progress`) and stops while the
  governor's pause flag exists (L2 and up), so a pause moves the remaining steps later by exactly the pause, on both sides. While
  paused no step is applied and no expectation is polled (GV-8: both fork Docker).
- **Heal.** At the end of each side (before its collect, so before the A to B restore) and after an abort (at L0-L1), the faults are
  undone: containers unpaused, slurmd's hold file removed, the nodes resumed. The snapshot restore does not unpause a container.
- **Recorded, not interference.** The steps are written to `interference.jsonl` with `by: lab-run`; `Run.comparable()` skips them.
- **Expectations are information.** Their outcome per side is in the report; they never change the verdict.
- **No seed.** The engine has no randomness (`any` picks the first N nodes), so the run's seed changes nothing here.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from . import checks, lab, lab_nodes, lab_slurm, scenario, selectors
from .lab import LabError

FILE = "scenario.json"
OWN = "lab-run"  # benchctl.lab_interference.OWN
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")  # governor.control.SCENARIO_RE
TARGETS = ("slurm",)
NODE_KEYS = ("drain", "down", "resume", "power_off", "power_on", "slurmd_stop", "slurmd_start", "slurmd_restart")
CONTAINER_KEYS = ("power_off", "power_on", "slurmd_stop", "slurmd_start", "slurmd_restart")  # docker pause/unpause/exec of the node
STEP_KEYS = (*NODE_KEYS, "slurmctld_restart", "reason")
EXPECT_KINDS = ("slurm.node_state", "slurm.job_state", "slurm.stepd")
MIN_GAP_S = 1.0  # two steps on one node closer than this in lab time: the factor squashed them (the ADR 0050 one-second floor)
POLL_S = 0.5


def lab_node_list(name: str, sim: dict, root: Path | None = None) -> list[dict[str, Any]]:
    """The lab's node containers in the order `apply lab` makes them ([{service, host, gpus}]): the same downscale and plan."""
    b = lab.load_baseline(name, root)
    if b["slurm"] is None or not b["slurm"]["nodes"]:
        raise LabError(f"lab {name} has no node lines in slurm.conf: a scenario needs lab nodes to act on")
    scale = lab_slurm.downscale(lab_slurm.production_nodes(b["slurm"], b["topology"], b["gres"]), lab_slurm.settings(sim)["max_nodes"])
    return lab_nodes.plan(b, None, scale)["nodes"]


def pools(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {"cpu": [n["host"] for n in nodes if not n.get("gpus")], "gpu": [n["host"] for n in nodes if n.get("gpus")]}


def _names(do: dict) -> list[tuple[str, str]]:
    return [(k, n) for k in NODE_KEYS for n in (do.get(k) or []) if isinstance(do.get(k), list)]


def problems(sc: dict, hosts: set[str], factor: float, length_s: float) -> list[str]:
    """Why a resolved scenario cannot play in a lab run, one message per problem, or []."""
    out: list[str] = []
    seen: dict[str, list[tuple[float, float]]] = {}  # node -> [(production s, lab s)]
    last = 0.0
    for i, s in enumerate(sc.get("steps") or [], 1):
        t = s.get("target")
        do = s.get("do") or {}
        try:
            at = scenario.parse_duration(s["at"])
        except (KeyError, scenario.ScenarioError):
            out.append(f"step {i}: bad or missing 'at'")
            continue
        if t not in TARGETS:
            out.append(f"step {i}: target {t} cannot act on lab nodes")
            continue
        if bad := sorted(set(do) - set(STEP_KEYS)):
            out.append(f"step {i}: {', '.join(bad)} cannot play in a lab run (allowed: {', '.join(k for k in STEP_KEYS if k != 'reason')})")
        for _, n in _names(do):
            if n not in hosts:
                out.append(f"step {i}: {n} is not a lab node")
        lab_s = at / factor
        last = max(last, lab_s)
        for n in {n for _, n in _names(do)} | ({"slurmctld"} if do.get("slurmctld_restart") else set()):
            seen.setdefault(n, []).append((at, lab_s))
    if any("cannot act on lab nodes" in m for m in out):
        out.insert(0, "a lab run plays slurm steps only (the BMC farm, BCM, GPU exporter and the other parts know only the simulated nodes)")
    if last > length_s:
        out.append(f"the last step plays at {last:.0f} s of lab time, after the side ends ({length_s:.0f} s at time factor x{factor:g})")
    for n, times in sorted(seen.items()):
        times.sort()
        for (p0, l0), (p1, l1) in itertools.pairwise(times):
            if p1 > p0 and l1 - l0 < MIN_GAP_S:
                out.append(f"steps on {n} at {p0:g} s and {p1:g} s land {l1 - l0:.2f} s apart at time factor x{factor:g}; "
                           f"a lab run needs at least {MIN_GAP_S * factor:g} s of scenario time between them")
                break
    for e, exp in enumerate(sc.get("expect") or [], 1):
        out += [f"expect {e}: {m}" for m in scenario.expect_problems(exp)]
        if isinstance(exp, dict) and len(exp) == 1:
            (kind, args), = exp.items()
            if kind not in EXPECT_KINDS:
                out.append(f"expect {e}: {kind} cannot be checked on lab nodes (a lab run checks {', '.join(EXPECT_KINDS)})")
            elif isinstance(args, dict) and args.get("node") not in hosts:
                out.append(f"expect {e}: {args.get('node')} is not a lab node")
    return out


def _to_containers(do: dict, service: dict[str, str]) -> dict:
    return {k: ([service[n] for n in v] if k in CONTAINER_KEYS else v) for k, v in do.items()}


def freeze(name: str, scenario_name: str, sim: dict, factor: float, length_s: float, root: Path | None = None,
           scenarios: Path = scenario.SCENARIOS) -> dict[str, Any]:
    """The scenario as this run plays it (selectors resolved to lab nodes, container steps on service names), or LabError naming
    every problem. Nothing is written; `write` puts it in the run folder."""
    if not (isinstance(scenario_name, str) and NAME.fullmatch(scenario_name)):
        raise LabError(f"--scenario must be a scenario name such as drain-mid-job, not {scenario_name!r}")
    path = scenarios / f"{scenario_name}.yaml"
    if not path.is_file():
        raise LabError(f"no scenario {scenario_name} in scenarios/ (see: benchctl scenario list)")
    raw = yaml.safe_load(path.read_text()) or {}
    nodes = lab_node_list(name, sim, root)
    try:
        sc = selectors.resolve_scenario(raw, sim, pools(nodes))
    except scenario.ScenarioError as e:
        raise LabError(f"scenario {scenario_name} cannot play in a lab run: {e}") from e
    if bad := problems(sc, {n["host"] for n in nodes}, factor, length_s):
        raise LabError(f"scenario {scenario_name} cannot play in a lab run: " + "; ".join(bad[:6]) + (f" (and {len(bad) - 6} more)" if len(bad) > 6 else ""))
    service = {n["host"]: n["service"] for n in nodes}
    steps = [{"at": scenario.parse_duration(s["at"]), "target": s["target"], "do": _to_containers(s.get("do") or {}, service)} for s in sc.get("steps") or []]
    expects = []
    for exp in sc.get("expect") or []:
        (kind, args), = exp.items()
        expects.append({kind: {**args, "node": service[args["node"]]} if kind == "slurm.stepd" else args})  # pgrep runs in the container
    touched = sorted({n for s in sc.get("steps") or [] for _, n in _names(s.get("do") or {})})
    return {"name": raw.get("name", scenario_name), "file": scenario_name, "ref": raw.get("ref"), "factor": factor, "steps": steps,
            "expect": expects, "heal": {"resume": touched, "unpause": sorted({service[n] for s in sc.get("steps") or [] for n in (s.get("do") or {}).get("power_off") or []}),
                                        "release": sorted({service[n] for s in sc.get("steps") or [] for n in (s.get("do") or {}).get("slurmd_stop") or []})},
            "last_lab_s": round(max((st["at"] for st in steps), default=0.0) / factor, 1),
            "hosts": {service[n]: n for n in sorted(service)}}  # service -> Slurm name: the report names nodes as Slurm does (the anonymizer maps those)


def text(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def summary(doc: dict, sha: str) -> dict[str, Any]:
    """What run.json keeps about the attached scenario."""
    return {"name": doc["name"], "file": doc["file"], "ref": doc.get("ref"), "sha256": sha, "steps": len(doc["steps"]),
            "expects": len(doc["expect"]), "last_lab_s": doc["last_lab_s"]}


def write(folder: Path, doc: dict) -> dict[str, Any]:
    t = text(doc)
    (folder / FILE).write_text(t)
    (folder / FILE).chmod(0o444)  # frozen: both sides and a restore-only child read this copy
    return summary(doc, hashlib.sha256(t.encode()).hexdigest())


def load(folder: Path, sha: str) -> dict:
    """The frozen scenario, refused when it changed since `--prepare` (both sides must play the same steps)."""
    t = (folder / FILE).read_text()
    if hashlib.sha256(t.encode()).hexdigest() != sha:
        raise LabError(f"{FILE} of run {folder.name} changed after it was frozen; both sides must play the same steps, start a new run")
    return json.loads(t)


def heal_commands(doc: dict) -> list[list[str]]:
    """Undo what the scenario can leave behind (the Jepsen final-generator pattern): unpause the containers it powered off, remove
    slurmd's hold file where it stopped slurmd, then resume every node it named. Each is harmless when nothing is left to undo."""
    h = doc.get("heal") or {}
    cmds = [["docker", "unpause", f"workbench-{s}-1"] for s in h.get("unpause", [])]
    cmds += [["docker", "exec", f"workbench-{s}-1", "rm", "-f", scenario.HOLD] for s in h.get("release", [])]
    cmds += [["docker", "exec", scenario.SLURMCTLD, "scontrol", "update", f"nodename={n}", "state=resume"] for n in h.get("resume", [])]
    return cmds


def heal(doc: dict, runner: Callable[[list[str]], Any] = checks.run) -> dict[str, Any]:
    """Run `heal_commands`; a failure is counted, not raised (an idle node refuses `resume`, a running container `unpause`)."""
    cmds = heal_commands(doc)
    failed = 0
    for c in cmds:
        try:
            rc = getattr(runner(c), "returncode", 0)
        except (OSError, ValueError):
            rc = 1
        failed += bool(rc) and c[1] == "exec" and "rm" in c  # `unpause` and `resume` refuse what is already undone: not a failure
    return {"commands": len(cmds), "failed": failed}


class Player:
    """Plays a frozen scenario on the replay clock of one side, in a thread (the driver's output is read in the caller's thread).

    `sync(replay_s)` is called with every `@progress` of the driver; the first one starts the scenario, so its time 0 is the replay's.
    Between two, the clock runs with the host's monotonic clock unless `paused()` (the governor's L2 flag, the one the driver reads)."""

    def __init__(self, doc: dict, paused: Callable[[], bool], aborted: Callable[[], bool], runner: Callable = checks.run,
                 mono: Callable[[], float] = time.monotonic, real_sleep: Callable[[float], None] = time.sleep,
                 play: Callable[..., scenario.Report] = scenario.run, start_thread: Callable[[Callable[[], None]], Any] | None = None):
        self.doc, self.paused, self.aborted, self.runner, self.mono, self.real_sleep, self.play = doc, paused, aborted, runner, mono, real_sleep, play
        self.start_thread = start_thread or (lambda fn: _thread(fn))
        self.t, self.last, self.started = 0.0, None, False
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.steps: list[dict] = []
        self.report: scenario.Report | None = None
        self.error: str | None = None
        self._handle: Any = None

    def now(self) -> float:
        with self.lock:
            m = self.mono()
            if self.last is not None and not self.paused():
                self.t += m - self.last
            self.last = m if self.last is not None else None
            return self.t

    def sync(self, replay_s: float) -> None:
        with self.lock:
            self.t, self.last = max(0.0, float(replay_s or 0.0)), self.mono()
        if not self.started:
            self.started = True
            self._handle = self.start_thread(self._run)

    def should_stop(self) -> bool:
        return self.stop.is_set() or self.aborted()

    def sleep(self, s: float) -> None:
        """A slice of real time; while paused it does not return, so no step is applied and no expectation polled (GV-8)."""
        self.real_sleep(min(max(0.0, s), POLL_S))
        while self.paused() and not self.should_stop():
            self.now()  # keeps the clock's last reading fresh, so the pause is not counted when it ends (error at most one slice)
            self.real_sleep(POLL_S)

    def named(self, text: str) -> str:
        """A label with container service names (`lab-node-NNN`, as a whole word) put back to the Slurm names, so a report names each
        node one way and `export lab-report --anonymize` maps all of them. A tool's message (`detail`) is left alone: a container name
        such as `workbench-lab-node-002-1` reveals nothing and must not become one the anonymizer cannot see."""
        hosts = self.doc.get("hosts") or {}
        return re.sub(r"(?<![A-Za-z0-9_.-])lab-node-\d{3}(?![A-Za-z0-9_-])", lambda m: hosts.get(m.group(0), m.group(0)), text)

    def _run(self) -> None:
        def on_step(step: scenario.Step, o: scenario.Outcome) -> None:
            self.steps.append({"at": step.at, "target": step.target, "label": self.named(o.label), "status": o.status, "detail": o.detail[-200:]})
        try:
            self.report = self.play(self.doc, speed=float(self.doc["factor"]), dilation=1.0, say=lambda m: None, sleep=self.sleep, clock=self.now,
                                    runner=self.runner, host=True, should_stop=self.should_stop, on_step=on_step, by=OWN)
        except Exception as e:  # noqa: BLE001 - the replay goes on; the report says the scenario failed
            self.error = f"{type(e).__name__}: {e}"[:300]

    def finish(self, timeout: float = 60.0) -> dict[str, Any]:
        """Stop the scenario (the side's replay ended) and return what it did: steps applied, expectations, what was not reached."""
        self.stop.set()
        if self._handle is not None and hasattr(self._handle, "join"):
            self._handle.join(timeout)
        expects = [{"label": self.named(o.label), "status": o.status, "detail": o.detail} for o in (self.report.expects if self.report else [])]
        done = {e["label"] for e in expects}
        for exp in self.doc.get("expect") or []:
            (kind, args), = exp.items()
            label = self.named(f"{kind} {json.dumps({k: v for k, v in args.items() if k != 'within'}, sort_keys=True)}")
            if label not in done:
                expects.append({"label": label, "status": "not reached", "detail": "the side ended first"})
        return {"started": self.started, "steps": self.steps, "planned_steps": len(self.doc.get("steps") or []), "expects": expects,
                "error": self.error}


def _thread(fn: Callable[[], None]) -> threading.Thread:
    t = threading.Thread(target=fn, daemon=True, name="lab-scenario")
    t.start()
    return t


def expect_table(result: dict[str, dict | None]) -> list[dict[str, Any]]:
    """One row per expectation with its status on each side and whether the sides differ (information: never the verdict)."""
    rows: dict[str, dict[str, Any]] = {}
    for side, res in sorted(result.items()):
        for e in (res or {}).get("expects") or []:
            rows.setdefault(e["label"], {"label": e["label"]})[side] = e["status"]
    for r in rows.values():
        r["differs"] = len({r.get(s) for s in result}) > 1
    return list(rows.values())
