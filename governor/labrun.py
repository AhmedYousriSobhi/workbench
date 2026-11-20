"""Config lab run supervision in the governor (issue 554, ADR 0050 addendum; owner decisions 2 and round 3): `lab-run`, `lab-stop`, status.

No new daemon (SPEC C-24): the work a run needs is started by one command and ends with it. A supervising thread here, a run-scoped
child the governor starts, tracks by pid and waits for (`benchctl run lab NAME --run RID --execute`: snapshot, apply, collect,
restore), and the replay driver, an unlabelled P5 one-shot container. The governor imports nothing of benchctl: it reads the run's
files and runs the CLI.

Phases of `active` (one at a time; `status.lab.state` shows them):
- `preparing`: set at the top of `start` (so two requests cannot both start), which answers at once (W-13, like `apply-plan`).
  A thread runs `benchctl run lab NAME --prepare` (freezes the jobs, prints the lab node count), admits `lab_nodes x 13 + 250` MB
  (GV-9, computed here), then spawns the child. A refusal is kept as `refused` with its reason until the next start.
- `running`: the thread maps the level to flags once a second: L2 and up writes `flags/lab.paused` (the driver stops its replay
  clock, the child waits before its next Docker step), below L2 removes it; L3 and up writes `flags/lab.abort`. Nothing else.
- `recovering`: after a governor restart. `run-active.json` names the old child's pid and start time; it is asked to abort, and if
  it is gone while a restore is owed, a restore-only child (`--recover`) removes the driver containers and restores the snapshot
  once the level is at L0-L1. The same commands are refused as during a run until that is done.

`stop` writes the abort flag at any level; `stop(force=True)` (from `benchctl down --force`) also writes `flags/lab.norestore`:
the run aborts, skips the restore and keeps the snapshot. During a replay an operator's node action is recorded in the run's
`interference.jsonl`, which marks the run not comparable.
"""
from __future__ import annotations

import json
import os
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
PAUSE_FLAG, ABORT_FLAG, NORESTORE_FLAG = "lab.paused", "lab.abort", "lab.norestore"
ACTIVE = "run-active.json"  # compose/generated/lab/run-active.json: the run this governor supervises, its child's pid and start time
INTERFERENCE = "interference.jsonl"
RUN_ID = re.compile(r"\d{8}-\d{6}(-\d+)?")
MAX_RUN_S = 2 * (2 * 6 * 3600) + 2 * 3600  # two sides of the longest target, each allowed twice its length, plus two hours
PRESSURE = "aborted under memory pressure (L{level})"
RESTARTED = "the governor restarted during the run"
REPLAYING = ("replay_a", "replay_b")


def admission_mb(lab_nodes: int) -> int:
    return int(lab_nodes) * LAB_NODE_MB + REPLAY_MB


def proc_start(pid: int, proc: Path = Path("/proc")) -> str | None:
    """A process's start time (field 22 of /proc/PID/stat), so a reused pid is not taken for the run's child; None when it is gone."""
    try:
        stat = (proc / str(int(pid)) / "stat").read_text()
    except (OSError, ValueError):
        return None
    fields = stat.rsplit(")", 1)[-1].split()
    return fields[19] if len(fields) > 19 else None


class LabRun:
    """One supervised run at a time. `spawn(argv)` starts `benchctl ARGV` and returns a process with `pid` and `poll()`; `reader(argv)`
    runs a short `benchctl ARGV` and returns its stdout. Clock, sleep, the thread starter and the /proc reader are injectable."""

    def __init__(self, workdir: Path, level: Callable[[], int], record: Callable[[str], None], admit: Callable[[int], dict],
                 reader: Callable[[list[str]], str], spawn: Callable[[list[str]], Any], interval: float = 1.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 start_thread: Callable[[Callable[[], None]], None] | None = None,
                 started: Callable[[int], str | None] = proc_start):
        self.workdir = Path(workdir)
        self.level, self.record, self.admit, self.reader, self.spawn = level, record, admit, reader, spawn
        self.interval, self.clock, self.sleep, self.started = interval, clock, sleep, started
        self.start_thread = start_thread or (lambda fn: threading.Thread(target=fn, daemon=True, name="lab-run").start())
        self.active: dict | None = None  # {phase, name, run, ab, admit_mb}
        self.last: dict | None = None  # the last refusal, kept until the next start
        self._proc: Any = None
        self._cancel: str | None = None  # `stop` while preparing
        self._lock = threading.Lock()

    # ---- paths and flags
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

    def _owned(self, p: Path) -> None:
        """A file the root service writes in the checkout belongs to the checkout's owner (the #476 lesson)."""
        if os.geteuid() == 0:
            st = self.workdir.stat()
            os.chown(p, st.st_uid, st.st_gid)

    def busy(self) -> str | None:
        """Why a cluster-changing command must wait now (a run in any phase), or None."""
        a = self.active
        if not a:
            return None
        if a["phase"] == "recovering":
            return f"lab run {a['run']} of {a['name']} is being recovered after a governor restart (restore of its snapshot)"
        return f"lab run {a.get('run') or '(preparing)'} of {a['name']} is in progress"

    # ---- the verbs
    def start(self, name: str, ab: bool = False, target_hours: float | None = None, seed: int | None = None, scenario: str | None = None) -> dict:
        """Answers at once (W-13): `preparing`, then the thread prepares, admits and spawns; `status.lab` shows the outcome. A `scenario`
        (its name already checked by the control socket) must be a file in the checkout's scenarios/ folder."""
        if scenario is not None and not (self.workdir / "scenarios" / f"{scenario}.yaml").is_file():
            return {"ok": False, "error": f"no scenario {scenario} in scenarios/ (see: benchctl scenario list)"}
        with self._lock:
            if why := self.busy():
                return {"ok": False, "error": f"{why}: one at a time (benchctl stop lab {self.active['name']} ends it)"}
            if (lvl := self.level()) > RUN_MAX_LEVEL:
                return {"ok": False, "error": f"refused at L{lvl}: lab runs start only at L0-L1 (GV-8)"}
            self.active, self.last, self._cancel = {"phase": "preparing", "name": name, "run": None, "ab": bool(ab), "admit_mb": None}, None, None
            self._flag(ABORT_FLAG, None)
            self._flag(PAUSE_FLAG, None)
            self._flag(NORESTORE_FLAG, None)
        self.record(f"lab-run={name}: preparing ({'A/B' if ab else 'candidate only'})")
        self.start_thread(lambda: self._launch(name, ab, target_hours, seed, scenario))
        return {"ok": True, "started": True, "state": "preparing", "name": name}

    def _refuse(self, name: str, why: str) -> None:
        self.record(f"lab-run={name}: {why}")
        self.last = {"name": name, "state": "refused", "reason": why}
        self.active = None

    def _launch(self, name: str, ab: bool, target_hours: float | None, seed: int | None, scenario: str | None = None) -> None:
        argv = ["run", "lab", name, "--prepare", "-o", "json"] + (["--ab"] if ab else [])
        argv += (["--target-hours", f"{float(target_hours):g}"] if target_hours is not None else []) + (["--seed", str(int(seed))] if seed is not None else [])
        argv += ["--scenario", scenario] if scenario is not None else []
        try:
            prep = json.loads(self.reader(argv))
            run_id, nodes = str(prep["run"]), int(prep["lab_nodes"])
        except Exception as e:  # noqa: BLE001 - the CLI's own message (no lab, no trace, a bad option) is the answer
            return self._refuse(name, f"refused: {str(e)[:300]}")
        if not RUN_ID.fullmatch(run_id):
            return self._refuse(name, f"refused: unexpected run id {run_id!r}")
        mb = admission_mb(nodes)
        adm = self.admit(mb) if not self._cancel else {"ok": False, "error": self._cancel}
        if not adm.get("ok"):
            shutil.rmtree(self.folder(name, run_id), ignore_errors=True)  # prepared seconds ago, never started
            return self._refuse(name, f"{adm.get('error')} (a run needs {mb} MB: {nodes} lab nodes x {LAB_NODE_MB} MB + {REPLAY_MB} MB)")
        try:
            self._proc = self.spawn(["run", "lab", name, "--run", run_id, "--execute"])
        except Exception as e:  # noqa: BLE001
            return self._refuse(name, f"refused: could not start the run: {e}")
        self.active.update(phase="running", run=run_id, admit_mb=mb)
        self._write_active(name, run_id, ab, mb)
        self.record(f"lab-run={name}: run {run_id} started ({'A/B' if ab else 'candidate only'}, admitted {mb} MB)")
        self.supervise()

    def _write_active(self, name: str, run_id: str, ab: bool, mb: int) -> None:
        pid = getattr(self._proc, "pid", None)
        p = self.labs / ACTIVE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"name": name, "run": run_id, "ab": bool(ab), "admit_mb": mb, "pid": pid,
                                 "pid_start": self.started(pid) if pid else None}) + "\n")
        self._owned(p)

    def stop(self, name: str, force: bool = False) -> dict:
        a = self.active
        if not a or a["name"] != name:
            return {"ok": False, "error": f"no run of lab {name} is in progress"}
        if force:
            self._flag(NORESTORE_FLAG, "benchctl down --force")
        if a["phase"] == "preparing":
            self._cancel = "stopped by the operator before it started"
            return {"ok": True, "stopping": True, "run": None}
        if a["phase"] == "recovering" and not force:
            return {"ok": False, "error": f"run {a['run']} of lab {name} is being restored after a governor restart: wait for it (benchctl status)"}
        self._flag(ABORT_FLAG, "stopped by the operator" + (" (benchctl down --force: the restore is skipped)" if force else " (benchctl stop lab)"))
        self.record(f"lab-stop={name}: run {a['run']} asked to stop" + (" (forced, no restore)" if force else ""))
        return {"ok": True, "stopping": True, "run": a["run"], "force": force}

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

    def _watch(self, alive: Callable[[], bool]) -> None:
        started = self.clock()
        while alive():
            try:
                self.tick()
            except OSError as e:  # a flag write failed: say so, keep supervising
                self.record(f"lab-run: flag write failed: {e}")
            if self.clock() - started > MAX_RUN_S and not (self.flags / ABORT_FLAG).exists():
                self._flag(ABORT_FLAG, f"the run took longer than {MAX_RUN_S // 3600} h")
            self.sleep(self.interval)

    def supervise(self) -> None:
        self._watch(lambda: self._proc is not None and self._proc.poll() is None)
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

    # ---- recovery after a governor restart
    def _run_record(self, name: str, run_id: str) -> dict:
        try:
            return json.loads((self.folder(name, run_id) / "run.json").read_text())
        except (OSError, ValueError):
            return {}

    def settled(self, name: str, run_id: str) -> bool:
        """The old run needs nothing more: done, or aborted with no restore owed."""
        r = self._run_record(name, run_id)
        if r.get("state") == "done":
            return True
        if r.get("state") != "aborted" or r.get("restore_pending"):
            return False
        return not (r.get("ab") and r.get("snapshot") is not None and r.get("restored_after_abort") is not True)

    def recover(self) -> str | None:
        """At governor start: a run that was active has lost its supervisor. It is asked to abort; until it is settled (its child
        restores, or a restore-only child does once the old child is gone) the phase is `recovering` and the same commands are refused."""
        self._flag(PAUSE_FLAG, None)  # the ladder starts at L0: a pause flag left by a dead governor is stale
        try:
            old = json.loads((self.labs / ACTIVE).read_text())
            name, run_id = str(old["name"]), str(old["run"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not (RUN_ID.fullmatch(run_id) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", name)):
            (self.labs / ACTIVE).unlink(missing_ok=True)
            return None
        self.active = {"phase": "recovering", "name": name, "run": run_id, "ab": bool(old.get("ab")), "admit_mb": old.get("admit_mb")}
        if not (self.flags / ABORT_FLAG).exists():
            self._flag(ABORT_FLAG, RESTARTED)
        msg = f"lab-run: run {run_id} of {name} lost its supervisor (governor restart): recovering"
        self.record(msg)
        pid, pid_start = old.get("pid"), old.get("pid_start")
        self.start_thread(lambda: self._recover(name, run_id, pid, pid_start))
        return msg

    def _recover(self, name: str, run_id: str, pid: int | None, pid_start: str | None) -> None:
        def old_alive() -> bool:
            return bool(pid) and pid_start is not None and self.started(pid) == pid_start
        self._watch(lambda: old_alive() and not self.settled(name, run_id))  # the old child aborts and restores itself
        if not self.settled(name, run_id):
            try:
                self._proc = self.spawn(["run", "lab", name, "--run", run_id, "--recover"])
                self.record(f"lab-run: run {run_id} of {name}: restore-only child started")
                self._watch(lambda: self._proc.poll() is None)
            except Exception as e:  # noqa: BLE001
                self.record(f"lab-run: run {run_id} of {name}: could not start the restore-only child: {e}")
        self._end("recovered" if self.settled(name, run_id) else "NOT recovered: `benchctl remove lab " + name + "` returns to the plain cluster")

    # ---- interference (owner round 3, Q3)
    def interfere(self, action: str, target: str | None) -> bool:
        """An operator's node action during a replay: one line in the run's interference.jsonl (the run is then not comparable)."""
        a = self.active
        if not a or a["phase"] != "running" or not a.get("run"):
            return False
        prog = self.progress() or {}
        if prog.get("state") not in REPLAYING:
            return False
        p = self.folder(a["name"], a["run"]) / INTERFERENCE
        try:
            new = not p.exists()
            with p.open("a") as f:
                f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "action": action, "target": target,
                                    "side": prog.get("side"), "by": "governor"}) + "\n")
            if new:
                self._owned(p)
        except OSError as e:
            self.record(f"lab-run: could not record interference: {e}")
            return False
        self.record(f"lab-run: {action} {target or ''} during replay {prog.get('side')}: run {a['run']} is not comparable")
        return True

    # ---- status
    def progress(self) -> dict | None:
        a = self.active
        if not a:
            return dict(self.last) if self.last else None
        if a["phase"] == "preparing":
            return {"name": a["name"], "state": "preparing", "run": None, "ab": a["ab"], "active": True}
        try:
            p = json.loads((self.folder(a["name"], a["run"]) / "progress.json").read_text())
        except (OSError, ValueError):
            p = {"state": "starting"}
        keys = ("state", "percent", "submitted", "running", "pending", "paused_s", "run", "side", "paused", "comparable", "reason",
                "restore_pending", "interference")
        out = {k: p.get(k) for k in keys}
        out.update(name=a["name"], run=a["run"], ab=a["ab"], admit_mb=a["admit_mb"], active=True)
        if a["phase"] == "recovering":
            out.update(state="recovering", run_state=p.get("state"))
        return out
