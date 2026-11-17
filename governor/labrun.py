"""Config lab run supervision in the governor (issue 554 step 4, ADR 0050 addendum, owner decision 2): `lab-run`, `lab-stop`, status.

Decision 2 allows a thread in the governor that only supervises flags and progress; the long work is the replay driver, an
unlabelled P5 one-shot container (decision 1). The steps between the replays (snapshot, apply, collect, restore) run in one
run-scoped child process, `benchctl run lab NAME --run RID --execute`, which this module starts after admission and reaps; it
is not a daemon and ends with the run. The governor imports nothing of benchctl: it reads the run's files and runs the CLI.

- `start`: refused at L2 and up (GV-8), when a run is already active, or when the run's memory does not fit (GV-9). The memory is
  computed here from the prepared run's lab node count: `lab_nodes x 13 MB + 250 MB` (ADR 0050 addendum, issue 551). A short
  `benchctl run lab NAME --prepare` freezes the jobs and prints that count; a refused run's folder is removed again.
- the thread, once a second: L2 and up writes `flags/lab.paused` (the driver stops its replay clock; the run records the pause),
  below L2 removes it; L3 and up writes `flags/lab.abort` ("aborted under memory pressure"): the driver cancels its jobs and the
  child restores the snapshot once the level is back at L0-L1 (GV-8: the restore forks Docker). Writing a small file is all it
  does at L3. The driver never reads `arrivals.paused` (L1).
- `stop`: writes the abort flag; allowed at every level, like `capture-stop`.
- after a governor restart a run that was active gets the abort flag (its supervisor is gone) and one audit line.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

LAB_NODE_MB, REPLAY_MB = 13, 250  # benchctl.lab_slurm.LAB_NODE_MB and REPLAY_MB (a test keeps them in step)
RUN_MAX_LEVEL = 1  # a run starts only at L0-L1 (GV-8)
PAUSE_LEVEL, ABORT_LEVEL = 2, 3  # ADR 0050: L2 pauses the replay clock, L3 aborts the run
PAUSE_FLAG, ABORT_FLAG = "lab.paused", "lab.abort"
ACTIVE = "run-active.json"  # compose/generated/lab/run-active.json: which run this governor supervises
RUN_ID = re.compile(r"\d{8}-\d{6}(-\d+)?")
MAX_RUN_S = 2 * (2 * 6 * 3600) + 2 * 3600  # two sides of the longest target, each allowed twice its length, plus two hours
PRESSURE = "aborted under memory pressure (L{level})"


def admission_mb(lab_nodes: int) -> int:
    return int(lab_nodes) * LAB_NODE_MB + REPLAY_MB


class LabRun:
    """One supervised run at a time. `spawn(argv)` starts `benchctl ARGV` and returns a process with poll(); `reader(argv)` runs a
    short `benchctl ARGV` and returns its stdout. Clock, sleep and the thread starter are injectable for the tests."""

    def __init__(self, workdir: Path, level: Callable[[], int], record: Callable[[str], None], admit: Callable[[int], dict],
                 reader: Callable[[list[str]], str], spawn: Callable[[list[str]], Any], interval: float = 1.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 start_thread: Callable[[Callable[[], None]], None] | None = None):
        self.workdir = Path(workdir)
        self.level, self.record, self.admit, self.reader, self.spawn = level, record, admit, reader, spawn
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.start_thread = start_thread or (lambda fn: threading.Thread(target=fn, daemon=True, name="lab-run").start())
        self.active: dict | None = None  # {name, run, ab, admit_mb}
        self._proc: Any = None
        self._lock = threading.Lock()  # one start at a time

    # ---- paths
    @property
    def flags(self) -> Path:
        return self.workdir / "compose" / "generated" / "flags"

    @property
    def labs(self) -> Path:
        return self.workdir / "compose" / "generated" / "lab"

    def folder(self, name: str, run_id: str) -> Path:
        return self.labs / name / "reports" / run_id

    def _flag(self, name: str, text: str | None) -> None:
        p = self.flags / name
        if text is None:
            p.unlink(missing_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text + "\n")

    # ---- the verbs
    def start(self, name: str, ab: bool = False, target_hours: float | None = None, seed: int | None = None) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "error": "a lab run is being started: wait for it"}
        try:
            if self.active:
                return {"ok": False, "error": f"lab run {self.active['run']} of {self.active['name']} is in progress: one at a time "
                                              f"(benchctl stop lab {self.active['name']} ends it)"}
            if (lvl := self.level()) > RUN_MAX_LEVEL:
                return {"ok": False, "error": f"refused at L{lvl}: lab runs start only at L0-L1 (GV-8)"}
            argv = ["run", "lab", name, "--prepare", "-o", "json"] + (["--ab"] if ab else [])
            argv += (["--target-hours", f"{float(target_hours):g}"] if target_hours is not None else []) + (["--seed", str(int(seed))] if seed is not None else [])
            try:
                prep = json.loads(self.reader(argv))
                run_id, nodes = str(prep["run"]), int(prep["lab_nodes"])
            except Exception as e:  # noqa: BLE001 - the CLI's own message (no lab, no trace, a bad option) is the answer
                return {"ok": False, "error": f"lab-run {name}: {str(e)[:300]}"}
            if not RUN_ID.fullmatch(run_id):
                return {"ok": False, "error": f"lab-run {name}: unexpected run id {run_id!r}"}
            mb = admission_mb(nodes)
            adm = self.admit(mb)
            if not adm.get("ok"):
                shutil.rmtree(self.folder(name, run_id), ignore_errors=True)  # prepared seconds ago, never started
                self.record(f"lab-run={name}: {adm.get('error')} ({nodes} lab nodes x {LAB_NODE_MB} MB + {REPLAY_MB} MB)")
                return {"ok": False, "error": f"{adm.get('error')} (a run needs {mb} MB: {nodes} lab nodes x {LAB_NODE_MB} MB + {REPLAY_MB} MB)"}
            self._flag(ABORT_FLAG, None)
            self._flag(PAUSE_FLAG, None)
            self.active = {"name": name, "run": run_id, "ab": bool(ab), "admit_mb": mb}
            (self.labs / ACTIVE).write_text(json.dumps(self.active) + "\n")
            try:
                self._proc = self.spawn(["run", "lab", name, "--run", run_id, "--execute"])
            except Exception as e:  # noqa: BLE001
                self._end(f"FAILED to start: {e}")
                return {"ok": False, "error": f"lab-run {name}: could not start the run: {e}"}
            self.record(f"lab-run={name}: run {run_id} started ({'A/B' if ab else 'candidate only'}, admitted {mb} MB)")
            self.start_thread(self.supervise)
            return {"ok": True, "started": True, "run": run_id, "admit_mb": mb, "ab": bool(ab)}
        finally:
            self._lock.release()

    def stop(self, name: str) -> dict:
        a = self.active
        if not a or a["name"] != name:
            return {"ok": False, "error": f"no run of lab {name} is in progress"}
        self._flag(ABORT_FLAG, "stopped by the operator (benchctl stop lab)")
        self.record(f"lab-stop={name}: run {a['run']} asked to stop")
        return {"ok": True, "stopping": True, "run": a["run"]}

    # ---- the supervising thread
    def tick(self) -> None:
        """One supervision step: the level becomes the pause and abort flags."""
        lvl = self.level()
        paused = (self.flags / PAUSE_FLAG).exists()
        if lvl >= PAUSE_LEVEL and not paused:
            self._flag(PAUSE_FLAG, f"L{lvl}")
            self.record(f"lab-run: paused at L{lvl}")
        elif lvl < PAUSE_LEVEL and paused:
            self._flag(PAUSE_FLAG, None)
            self.record(f"lab-run: resumed at L{lvl}")
        if lvl >= ABORT_LEVEL and not (self.flags / ABORT_FLAG).exists():
            self._flag(ABORT_FLAG, PRESSURE.format(level=lvl))
            self.record(f"lab-run: {PRESSURE.format(level=lvl)}; the snapshot is restored once the level is back at L0-L1")

    def supervise(self) -> None:
        started = self.clock()
        while self._proc is not None and self._proc.poll() is None:
            try:
                self.tick()
            except OSError as e:  # a flag write failed: say so once per step, keep supervising
                self.record(f"lab-run: flag write failed: {e}")
            if self.clock() - started > MAX_RUN_S and not (self.flags / ABORT_FLAG).exists():
                self._flag(ABORT_FLAG, f"the run took longer than {MAX_RUN_S // 3600} h")
            self.sleep(self.interval)
        rc = self._proc.poll() if self._proc is not None else None
        st = (self.progress() or {}).get("state", "unknown")
        self._end(f"ended {st}" + (f" (exit {rc})" if rc else ""))

    def _end(self, how: str) -> None:
        a = self.active
        self._flag(PAUSE_FLAG, None)
        (self.labs / ACTIVE).unlink(missing_ok=True)
        if a:
            self.record(f"lab-run={a['name']}: run {a['run']} {how}")
        self.active, self._proc = None, None

    def recover(self) -> str | None:
        """At governor start: a run that was active has lost its supervisor. Ask it to abort (its child, if alive, restores)."""
        self._flag(PAUSE_FLAG, None)  # the ladder starts at L0: a pause flag left by a dead governor is stale
        p = self.labs / ACTIVE
        try:
            old = json.loads(p.read_text())
        except (OSError, ValueError):
            return None
        p.unlink(missing_ok=True)
        self._flag(ABORT_FLAG, "the governor restarted during the run")
        msg = f"lab-run: run {old.get('run')} of {old.get('name')} lost its supervisor (governor restart): asked it to abort"
        self.record(msg)
        return msg

    # ---- status
    def progress(self) -> dict | None:
        a = self.active
        if not a:
            return None
        try:
            p = json.loads((self.folder(a["name"], a["run"]) / "progress.json").read_text())
        except (OSError, ValueError):
            p = {"state": "starting"}
        keys = ("state", "percent", "submitted", "running", "pending", "paused_s", "run", "side", "paused", "comparable", "reason",
                "restore_pending")
        out = {k: p.get(k) for k in keys}
        out.update(name=a["name"], run=a["run"], ab=a["ab"], admit_mb=a["admit_mb"], active=True)
        return out
