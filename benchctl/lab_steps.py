"""Config lab run, the host side of each state (issue 554 steps 3 and 4, ADR 0050): what `lab_run.drive` calls in the run's child.

The governor's `lab-run` starts `benchctl run lab NAME --run RID --execute` as its child once admission passed (ADR 0050 addendum,
owner decision 2: the governor's thread only supervises flags and progress). That child walks the run with `RunSteps`:

- apply(side): `lab_apply.apply`, the same code as `benchctl apply lab` (it is already the governor's child, so no second request);
- replay(side, run): the driver (`images/slurm-ctl/lab_driver.py`) in an unlabelled P5 one-shot container (decision 1), its
  `@progress` lines turned into the run's progress and pauses, the `lab_run` Prometheus file written for the side (decision 9);
- collect, snapshot, restore, discard: `lab_snapshot.StateSteps`;
- compare(run): pairs the sides by job key into `compare.json`; report(run): a short `report.json` (the full report and verdict are
  issue 555);
- abort_reason(): the governor's abort flag (L3, `benchctl stop lab`, a governor restart);
- wait_calm(run): before an abort's restore, wait until the governor is at L0-L1 (GV-8: the restore forks Docker and reloads MariaDB).

The `lab_run` label (decision 9, a relabel rule): while a side replays, `compose/generated/prometheus/lab-run/slurm.json` lists
slurmctld's four metrics paths with the hidden label `__lab_run` = `NAME/RUN/SIDE`; the static `lab-run` job in
observability/prometheus.yml keeps only targets that carry it and copies it to `lab_run`. Prometheus re-reads file_sd files by
itself, so no configuration is rewritten or reloaded per run. Only these series get the label (not sim-nodes, the GPU exporter or
the governor's own metrics). The file is written on the host by this step, not by the container, so nothing becomes root-owned.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import lab, lab_apply, lab_prom, lab_report, lab_run, lab_scenario, lab_slurm, lab_snapshot
from .lab import LabError

GENERATED = Path("compose/generated")
REPO = Path(__file__).resolve().parent.parent
DRIVER = REPO / "images" / "slurm-ctl" / "lab_driver.py"
IMAGE = "workbench/slurm-ctl:dev"
NETWORK = "workbench_default"
DRIVER_MB = 250  # ADR 0050: the replay's cap, the same 250 MB the governor admits
FLAGS = "flags"
PAUSE_FLAG, ABORT_FLAG, NORESTORE_FLAG = "lab.paused", "lab.abort", "lab.norestore"  # written by the governor (governor/labrun.py)
RESTARTED = "the governor restarted during the run"
PROM_DIR = "prometheus/lab-run"
SLURM_PATHS = ("jobs", "nodes", "partitions", "scheduler")  # benchctl.promtargets.SLURM_PATHS
SLURM_TARGET = "slurmctld:6817"
CALM_LEVEL = 1
CALM_POLL_S = 5.0
GOVERNOR_GONE_S = 300  # the governor did not answer this long: the restore is not run (the snapshot is kept)

Popen = Callable[[list[str]], Any]  # returns an object with .stdout (lines) and .wait()


def driver_name(run_id: str, side: str) -> str:
    return f"workbench-lab-driver-{run_id}-{side}"


def driver_argv(run: lab_run.Run, side: str, gen: Path = GENERATED) -> list[str]:
    """`docker run` of the driver: an unlabelled P5 one-shot (no `workbench.class` label, so the governor never freezes it before it
    can abort), memory-limited, unprivileged, on the cluster's network, every mount read-only."""
    g = gen.resolve()
    deadline = int(2 * max(60, run.r["length_s"]) + 3600)
    return ["docker", "run", "--rm", "--name", driver_name(run.r["run"], side), "--network", NETWORK,
            "--memory", f"{DRIVER_MB}m", "--memory-swap", f"{DRIVER_MB}m", "--pids-limit", "256",
            "-v", f"{(run.folder / 'jobs.json').resolve()}:/run/lab/jobs.json:ro",
            "-v", f"{g / 'slurm'}:/etc/slurm:ro",
            "-v", f"{g / 'secrets' / 'slurm'}:/run/workbench/slurm:ro",
            "-v", f"{g / FLAGS}:/run/workbench-flags:ro",
            "-v", f"{DRIVER}:/lab_driver.py:ro",
            "--entrypoint", "python3", IMAGE, "/lab_driver.py", "--deadline", str(deadline)]


def prom_targets(name: str, run_id: str, side: str) -> list[dict]:
    """The file_sd document that gives slurmctld's series the `lab_run` label for one side (decision 9)."""
    value = f"{name}/{run_id}/{side}"
    return [{"targets": [SLURM_TARGET], "labels": {"__metrics_path__": f"/metrics/{p}", "__lab_run": value, "slurm_path": p}}
            for p in SLURM_PATHS]


def parse_line(line: str) -> tuple[str, dict] | None:
    """`@progress {json}` or `@done {json}` from the driver; anything else (Docker's own output) is None."""
    for tag in ("@progress ", "@done "):
        if line.startswith(tag):
            try:
                doc = json.loads(line[len(tag):])
            except ValueError:
                return None
            return (tag.strip("@ "), doc) if isinstance(doc, dict) else None
    return None


def _quiet(argv: list[str]) -> int:
    return subprocess.run(argv, capture_output=True, check=False, timeout=60).returncode


def _popen(argv: list[str]) -> subprocess.Popen:
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


@dataclass
class RunSteps:
    """The real steps of `lab_run.drive` for one run. Everything slow or external is injectable for the tests."""

    name: str
    run_id: str
    env: lab_apply.Env | None = None
    root: Path | None = None
    gen: Path = GENERATED
    popen: Popen = _popen
    run_cmd: Callable[[list[str]], int] = _quiet
    governor: Callable[[dict], dict] | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    prom_get: Callable[[str], Any] | None = None  # reads Prometheus (tests inject a fake; None means the real one)
    state: lab_snapshot.StateSteps | None = None
    applied: list[str] = field(default_factory=list)
    player: Callable[..., lab_scenario.Player] = lab_scenario.Player  # plays the run's attached scenario (`--scenario`); tests inject a fake
    heal_runner: Callable[[list[str]], Any] | None = None  # runs the scenario's heal commands (None: the real Docker)

    def __post_init__(self) -> None:
        if self.state is None:
            self.state = lab_snapshot.StateSteps(lab_snapshot.snapshot_dir(self.name, self.run_id, self.root))

    # ---- the governor's flags
    def flag(self, name: str) -> str | None:
        try:
            return (self.gen / FLAGS / name).read_text()[:200]
        except FileNotFoundError:
            return None
        except OSError:
            return ""

    def skip_restore(self) -> str | None:
        """`benchctl down --force` (the governor's `lab.norestore` flag): the abort keeps the snapshot instead of restoring it."""
        why = self.flag(NORESTORE_FLAG)
        return None if why is None else (why.strip() or "forced")

    def remove_drivers(self) -> None:
        """A driver left by a run whose child is gone (a governor restart): removed by name, both sides."""
        for side in ("a", "b"):
            self.run_cmd(["docker", "rm", "-f", driver_name(self.run_id, side)])

    def abort_reason(self) -> str | None:
        """Checked before every state. While the governor's pause flag exists (L2 and up) it waits: the next state (snapshot, apply,
        collect, the A to B restore) forks Docker, which GV-8 keeps to L0-L1. A state already running finishes. The abort flag ends
        the wait at once."""
        while True:
            why = self.flag(ABORT_FLAG)
            if why is not None:
                return why.strip() or "aborted"
            if self.flag(PAUSE_FLAG) is None:
                return None
            self.sleep(CALM_POLL_S)

    # ---- the states
    def snapshot(self) -> dict:
        return self.state.snapshot()

    def restore(self) -> dict:
        """The A to B restore and an abort's restore: the attached scenario's faults are undone first (the snapshot does not unpause a
        container or resume a node slurmd never answered for); both run at L0-L1 only (the caller waited)."""
        healed = self.heal()
        res = self.state.restore()
        return {**res, "scenario_heal": healed} if healed is not None and isinstance(res, dict) else res

    def collect(self, side: str, run: lab_run.Run) -> dict[str, str]:
        """A side's accounting. At the end of a side that was not aborted, at L0-L1 (`abort_reason` waited), the attached scenario is
        healed first; otherwise it is healed later, after `wait_calm` (the restore, or `heal_after_abort`)."""
        if self.flag(ABORT_FLAG) is None and self.flag(PAUSE_FLAG) is None and (healed := self.heal()) is not None:
            run.r.setdefault("scenario_heal", {})[side] = healed
            run.save()
        return self.state.collect(side, run)

    # ---- the attached scenario (`run lab --scenario`, benchctl/lab_scenario.py)
    def _folder(self) -> Path:
        return lab_run.reports_dir(self.name, self.root) / self.run_id

    def _scenario(self) -> dict | None:
        try:
            sha = (json.loads((self._folder() / "run.json").read_text()).get("scenario") or {}).get("sha256")
        except (OSError, ValueError):
            return None
        return lab_scenario.load(self._folder(), sha) if sha else None

    def heal(self) -> dict | None:
        """Undo the attached scenario's faults (idempotent); None when the run has no scenario."""
        doc = self._scenario()
        if doc is None:
            return None
        return lab_scenario.heal(doc, self.heal_runner) if self.heal_runner else lab_scenario.heal(doc)

    def heal_after_abort(self, run: lab_run.Run) -> None:
        """An aborted run that owes no restore (candidate only): heal the scenario once the level is at L0-L1 (GV-8). Skipped by
        `benchctl down --force`, which takes the cluster down anyway."""
        if not run.r.get("scenario") or self.skip_restore():
            return
        try:
            self.wait_calm(run, key="heal_pending")
            run.r.setdefault("scenario_heal", {})["abort"] = self.heal()
        except Exception as e:  # noqa: BLE001 - the abort reason stays; the failed heal is recorded beside it
            run.r["scenario_heal_error"] = run.scrub(str(e)[-300:])
        run.save()

    def discard(self) -> None:
        self.state.discard()

    def apply(self, side: str) -> None:
        if self.env is None:
            raise LabError("no cluster environment to apply the lab with")
        res = lab_apply.apply(self.name, side, self.env)
        self.applied.append(side)
        for ln in res["lines"]:
            self.env.say(ln)

    def _prom(self, run: lab_run.Run, side: str | None) -> None:
        p = self.gen / PROM_DIR / "slurm.json"
        if side is None:
            p.unlink(missing_ok=True)
            return
        lab_run.write_atomic(p, json.dumps(prom_targets(self.name, run.r["run"], side), indent=2) + "\n")

    def replay(self, side: str, run: lab_run.Run) -> str | None:
        """Run the driver for one side; returns None when it replayed every job, else why it stopped (the run then aborts)."""
        run.jobs()  # the frozen file is checked before each side
        sc = run.r.get("scenario")
        player = self.player(lab_scenario.load(run.folder, sc["sha256"]), lambda: self.flag(PAUSE_FLAG) is not None,
                             lambda: self.flag(ABORT_FLAG) is not None) if sc else None
        self._prom(run, side)
        done: dict | None = None
        try:
            proc = self.popen(driver_argv(run, side, self.gen))
            for line in _lines(proc.stdout):
                got = parse_line(line.rstrip("\n"))
                if got is None:
                    continue
                kind, doc = got
                if player is not None and kind == "progress":
                    player.sync(float(doc.get("replay_s") or 0.0))  # the scenario's clock is the replay's (the first line starts it)
                self._progress(run, doc)
                if kind == "done":
                    done = doc
            rc = proc.wait()
        finally:
            if player is not None:
                run.r.setdefault("scenario_result", {})[side] = player.finish()
                run.save()
            self._prom(run, None)
            if run.r["paused_since"] is not None:
                run.resume()
        if done is None:
            return f"the replay driver ended without a result (exit {rc}): is the cluster up (benchctl up) and the slurm-ctl image built?"
        run.r["rejected"] = {**run.r.get("rejected", {}), side: int(done.get("rejected") or 0)}
        run.save()
        return done.get("reason")

    @staticmethod
    def _progress(run: lab_run.Run, doc: dict) -> None:
        if run.state not in lab_run.REPLAY:
            return
        if doc.get("paused") and run.r["paused_since"] is None:
            run.pause()
        elif not doc.get("paused") and run.r["paused_since"] is not None:
            run.resume()
        run.report_progress(int(doc.get("submitted") or 0), int(doc.get("running") or 0), int(doc.get("pending") or 0),
                            float(doc.get("replay_s") or 0.0))

    def compare(self, run: lab_run.Run) -> None:
        a = lab_run.parse_rows((run.side_dir("a") / "sacct.txt").read_text()) if (run.side_dir("a") / "sacct.txt").is_file() else []
        b = lab_run.parse_rows((run.side_dir("b") / "sacct.txt").read_text()) if (run.side_dir("b") / "sacct.txt").is_file() else []
        p = lab_run.pair(a, b)
        lab_run.write_atomic(run.folder / "compare.json", lab_run.dump({"pairs": len(p["pairs"]), "only_a": p["only_a"], "only_b": p["only_b"]}))

    def report(self, run: lab_run.Run) -> None:
        """`report.json` and `report.md`: the metrics of each side, the findings, the verdict and the suggestions (issue 555)."""
        r = run.r

        def rows(side: str) -> list[dict[str, str]] | None:
            f = run.side_dir(side) / "sacct.txt"
            return lab_run.parse_rows(f.read_text()) if f.is_file() else None

        a, b = (rows("a") if r["ab"] else None), rows("b") or []
        pairing = lab_run.pair(a, b) if a is not None else None
        sim = self.env.sim if self.env is not None else {}
        try:
            base = lab.load_baseline(self.name, self.root)
            time_scaled = lab_slurm.dilate_keys(base["slurm"]["keys"], float(r["factor"]))[1] if base["slurm"] else []
        except LabError:
            time_scaled = []
        meta = {"contract": lab_run.CONTRACT, "name": r["name"], "run": r["run"], "ab": r["ab"], "seed": r["seed"], "jobs": r["jobs"],
                "target_hours": r["target_hours"], "factor": r["factor"], "length_s": r["length_s"], "paused_s": run.paused_s(),
                "paused_side_s": r["paused_side_s"], "comparable": run.comparable(), "rejected": r.get("rejected", {}), "collected": r["collected"],
                "scenario": r.get("scenario"), "scenario_result": r.get("scenario_result") or {},
                "scenario_expects": lab_scenario.expect_table(r.get("scenario_result") or {}) if r.get("scenario") else []}
        doc = lab_report.build(meta, a, b, lab_report.thresholds(sim or {}), pairing, time_scaled, self._earlier(run), self._alerts(run),
                               self._sched(run))
        lab_run.write_atomic(run.folder / "report.json", lab_run.dump(doc))
        lab_run.write_atomic(run.folder / "report.md", lab_report.markdown(doc))

    def _earlier(self, run: lab_run.Run) -> list[str | None]:
        """The verdicts of this lab's earlier finished runs of the same frozen jobs (the repeats of one experiment)."""
        out = []
        for d in sorted(p for p in lab_run.reports_dir(self.name, self.root).glob("*") if p.is_dir() and p.name < run.r["run"]):
            try:
                if json.loads((d / "run.json").read_text()).get("jobs_sha256") == run.r["jobs_sha256"]:
                    out.append(json.loads((d / "report.json").read_text()).get("verdict"))
            except (OSError, ValueError):
                continue
        return out

    def _alerts(self, run: lab_run.Run) -> dict[str, dict | None]:
        """Alerts per side from Prometheus over the side's replay window; a side whose window is unknown or Prometheus is down is left out."""
        out: dict[str, dict | None] = {}
        for side in run.r["sides"]:
            try:
                out[side] = lab_prom.side_alerts(run.entered(f"replay_{side}"), run.entered(f"collect_{side}"), float(run.r["factor"]), **({"get": self.prom_get} if self.prom_get else {}))
            except lab_run.LabError:
                out[side] = None
        return out

    def _sched(self, run: lab_run.Run) -> dict[str, dict | None]:
        """Scheduler load per side from slurmctld's series labelled with the side's `lab_run` (decision 9), over the side's replay."""
        out: dict[str, dict | None] = {}
        for side in run.r["sides"]:
            try:
                start, end = run.entered(f"replay_{side}"), run.entered(f"collect_{side}")
            except lab_run.LabError:
                out[side] = None
                continue
            label = prom_targets(self.name, run.r["run"], side)[0]["labels"]["__lab_run"]
            out[side] = lab_prom.side_sched(label, start, end, **({"get": self.prom_get} if self.prom_get else {}))
        return out

    def wait_calm(self, run: lab_run.Run, key: str = "restore_pending") -> None:
        """Before an abort's restore: wait until the governor says L0 or L1 (GV-8). Shows `restore_pending` meanwhile. Raises when the
        governor stops answering for GOVERNOR_GONE_S (the restore is then not run and the snapshot is kept)."""
        if self.governor is None:
            return
        run.r[key] = True
        run.save()
        gone_since = None
        while True:
            try:
                level = self.governor({"cmd": "status"}).get("level")
                gone_since = None
            except OSError:
                level = None
                gone_since = gone_since if gone_since is not None else self.clock()
                if self.clock() - gone_since > GOVERNOR_GONE_S:
                    raise LabError(f"the governor did not answer for {GOVERNOR_GONE_S} s: the restore was not run; the snapshot is kept "
                                   f"(compose/generated/lab/{self.name}/snapshots/{self.run_id}); `benchctl remove lab {self.name}` "
                                   "returns to the plain cluster") from None
            if isinstance(level, int) and level <= CALM_LEVEL:
                break
            self.sleep(CALM_POLL_S)
        run.r[key] = False
        run.save()


def _lines(stream: Iterable[str] | None) -> Iterable[str]:
    return stream if stream is not None else ()


# ---------------------------------------------------------------- the CLI side (the governor's children)

def prepare(name: str, sim: dict, ab: bool, target_hours: float | None, seed: int, root: Path | None = None,
            scenario: str | None = None, scenarios: Path | None = None) -> dict:
    """Freeze a run's jobs (and its attached scenario) and record it (state idle): what the governor reads to compute admission.
    Changes nothing on the cluster. A scenario that cannot play on the lab nodes refuses the run and leaves no run folder."""
    lab.valid_name(name)
    if not lab.exists(name, root):
        raise LabError(f"no lab {name}: import it first (benchctl import lab {name} --from DIR)")
    run = lab_run.Run.create(name, sim, root, ab=ab, seed=seed, target_hours=target_hours)
    if scenario is not None:
        try:
            doc = lab_scenario.freeze(name, scenario, sim, float(run.r["factor"]), float(run.r["length_s"]), root,
                                      **({"scenarios": scenarios} if scenarios else {}))
        except LabError:
            shutil.rmtree(run.folder, ignore_errors=True)
            raise
        run.r["scenario"] = lab_scenario.write(run.folder, doc)
        run.save()
    return {"name": name, "run": run.r["run"], "lab_nodes": run.r["lab_nodes"], "ab": ab, "jobs": run.r["jobs"],
            "length_s": run.r["length_s"], "seed": seed, "scenario": (run.r.get("scenario") or {}).get("name")}


def recover(name: str, run_id: str, governor: Callable[[dict], dict] | None, root: Path | None = None,
            steps: RunSteps | None = None) -> lab_run.Run:
    """The restore-only child the governor starts after a restart when the old run's child is gone (owner decision round 3, Q1):
    remove the run's driver containers, end the run as aborted if it had not ended (collecting the side in progress), restore an
    A/B snapshot once the level is at L0-L1, and discard the snapshot when it was put back. Safe to run again."""
    run = lab_run.Run.load(name, run_id, root)
    steps = steps or RunSteps(name, run_id, root=root, governor=governor)
    steps.remove_drivers()
    if run.state not in lab_run.TERMINAL:
        lab_run.abort_and_restore(run, steps, RESTARTED)
    elif lab_run.owes_restore(run):
        lab_run.restore_after_abort(run, steps)
    elif run.state == "aborted":
        steps.heal_after_abort(run)  # an attached scenario's faults (idempotent: nothing to undo is not a failure)
    if run.r.get("restored_after_abort") is not False:
        steps.discard()
    return run


def execute(name: str, run_id: str, env: lab_apply.Env, governor: Callable[[dict], dict] | None, root: Path | None = None) -> lab_run.Run:
    """Walk one prepared run to its end (the governor's child). Returns the run in `done` or `aborted`."""
    run = lab_run.Run.load(name, run_id, root)
    if run.state != "idle":
        raise LabError(f"run {run_id} of lab {name} is {run.state}, not idle: start a new run")
    return lab_run.drive(run, RunSteps(name, run_id, env=env, root=root, governor=governor))
