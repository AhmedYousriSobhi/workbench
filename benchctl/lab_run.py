"""Config lab run: state machine, frozen jobs file, user remap, seed and A/B pairing (issue 554 steps 1 and 2, ADR 0049, 0050).

A run replays one lab's sampled trace on the cluster, once per side: A is the baseline, B the candidate. `run lab` without `--ab`
replays the candidate only (owner decision 7). Both sides read the same frozen `jobs.json`, written once when the run is created
and checked by its sha256 before each side, so they see exactly the same jobs at the same lab times.

Owner decisions of 2026-11-17 (issue 554) that this module carries:
- 5: users become `lab-u001...`; no production user name is written to `jobs.json`, the progress record or the report. The run
  path always maps (so `--keep-users` applies to `get lab` only: an implementation choice to confirm, ADR 0050 addendum). The mapping
  itself holds production names and is never stored; free text (an abort reason, a tool's error) has them replaced by `<user>`.
- 6: the run length is `--target-hours` / `lab.target_hours`. The dilation factor is `max(1, span / target)`, so a short trace runs
  for its own span, below the target; the run records both.
- 8: a seed, recorded in `jobs.json` and the run record; sampling uses it, and both sides read it from the same file.
- 3: the snapshot restore between A and B is internal to the run (`lab_snapshot`), never a governor verb. Run A's accounting is
  written to the report folder before the restore, which erases it (`Run.require_collected`).

Not here (later steps of #554): the driver that submits the jobs (step 3: it names each job by its `key`, so A and B pair by key,
not by Slurm job id, which the restore reuses), the governor verbs, thread and admission (step 4), the CLI (step 5), and the lab
node containers (#863).

TODO(#554, decision 9): the Prometheus `lab_run` label. The owner answered "both" (a relabel rule and an external label with a
reload); how the two combine is not decided, so nothing here sets it.

Layout: compose/generated/lab/NAME/reports/RUN/ holds run.json (the run record), progress.json (what governor `status` will show),
jobs.json and, per side, a/ and b/ (collected accounting). Snapshots live outside the report folder (lab_snapshot).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from . import lab, lab_replay, lab_slurm
from . import lab_import as li
from .lab import LabError

CONTRACT = 1
DEFAULT_SEED = 1  # the seed `get lab` samples with, so a run with no --seed replays the jobs the plan showed
SIDES = {"a": "baseline", "b": "candidate"}
COMPARABLE_PAUSE = 0.05  # a side paused longer than 5 % of the run length: not comparable (ADR 0050)

# States in run order. An A/B run walks AB_PATH, a candidate-only run CANDIDATE_PATH (decision 7: no snapshot, restore or compare).
AB_PATH = ("idle", "snapshot", "apply_baseline", "replay_a", "collect_a", "restore", "apply_candidate", "replay_b", "collect_b",
           "compare", "report", "done")
CANDIDATE_PATH = ("idle", "apply_candidate", "replay_b", "collect_b", "report", "done")
TERMINAL = ("done", "aborted")
REPLAY = {"replay_a": "a", "replay_b": "b"}
RUN_ID = re.compile(r"\d{8}-\d{6}(-\d+)?")

# What a job keeps from the trace: an allow-list, so a column such as JobName, WorkDir or Comment never reaches the jobs file.
STATE_KINDS = {"COMPLETED": "completed", "FAILED": "failed", "TIMEOUT": "timeout", "OUT_OF_MEMORY": "oom", "CANCELLED": "cancelled",
               "NODE_FAIL": "node_fail", "PREEMPTED": "preempted", "DEADLINE": "timeout", "BOOT_FAIL": "failed"}
OOM_EXIT = 137  # ADR 0050: an out-of-memory job exits 137 in the lab


def legal(path: tuple[str, ...], frm: str, to: str) -> bool:
    """A move is legal to the next state of the path, or to `aborted` from any state that has not ended."""
    if frm in TERMINAL:
        return False
    if to == "aborted":
        return True
    return frm in path and path.index(frm) + 1 < len(path) and path[path.index(frm) + 1] == to


# ---------------------------------------------------------------- jobs file

def _exit(text: str) -> tuple[int, int]:
    """sacct ExitCode `code:signal`; anything unreadable is 0:0."""
    a, _, b = (text or "0:0").partition(":")
    try:
        return int(a), int(b or 0)
    except ValueError:
        return 0, 0


def kind(state: str) -> str:
    word = (state or "").split()[0].rstrip("+") if (state or "").strip() else ""
    return STATE_KINDS.get(word.upper(), "other")


def job_record(i: int, job: dict[str, str], t0: datetime, factor: float, users: dict[str, str]) -> dict[str, Any]:
    """One replayed job, keyed `j0001`..., with lab user, scaled times and outcome. Nothing else from the trace is copied."""
    k = kind(job.get("State", ""))
    code, sig = _exit(job.get("ExitCode", ""))
    try:
        nodes = max(1, int(job.get("NNodes") or 1))
    except ValueError:
        nodes = 1
    return {"key": f"j{i:04d}", "user": users[job["User"]], "account": job.get("Account", ""), "qos": job.get("QOS", ""),
            "partition": job.get("Partition", ""), "tres": job.get("AllocTRES") or job.get("ReqTRES") or "", "nodes": nodes,
            **lab_replay.scale_job(job, t0, factor), "kind": k, "exit_code": OOM_EXIT if k == "oom" else code, "signal": sig}


def tokens(value: Any) -> Iterable[str]:
    """Every string value in a JSON-like object, whole and split at `, = : / space` (a user name hidden in a TRES or a list)."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from tokens(k)
            yield from tokens(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from tokens(v)
    elif isinstance(value, str):
        yield value
        yield from (t for t in re.split(r"[,=:/\s|]+", value) if t)


def leaks(value: Any, names: Iterable[str]) -> int:
    """How many production user names appear as a value or a token (the count only: the names themselves are never printed)."""
    names = set(names)
    return len(names & set(tokens(value)))


SYSTEM_USERS = {"root"}  # a dump lists root; it is no person's name and appears in lab accounting too


def production_users(name: str, root: Path | None = None) -> set[str]:
    """Every user name of the imported trace and account dump: none of them may reach the jobs file, progress or report."""
    base = lab.path(name, root) / "baseline"
    out = {j["User"] for j in lab.load_baseline(name, root)["trace"] if j.get("User")}
    if (base / "sacctmgr.dump").is_file():
        out |= {u["name"] for u in li.parse_sacctmgr_dump((base / "sacctmgr.dump").read_text())["users"]}
    return out - SYSTEM_USERS


def build_jobs(name: str, sim: dict, root: Path | None = None, seed: int = DEFAULT_SEED, target_hours: float | None = None,
               max_nodes: int | None = None) -> tuple[dict[str, Any], set[str]]:
    """The frozen jobs document of a run and the production user names it must not contain (for the leak checks)."""
    cfg = lab_slurm.settings(sim)
    target = float(target_hours if target_hours is not None else cfg["target_hours"])
    if not lab_slurm.MIN_TARGET_HOURS <= target <= lab_slurm.MAX_TARGET_HOURS:
        raise LabError(f"--target-hours must be from {lab_slurm.MIN_TARGET_HOURS} to {lab_slurm.MAX_TARGET_HOURS}, not {target}")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise LabError(f"the seed must be a whole number of 0 or more, not {seed!r}")
    b = lab.load_baseline(name, root)
    if b["slurm"] is None:
        raise LabError(f"lab {name} has no slurm.conf: the node shapes come from it")
    if not b["trace"]:
        raise LabError(f"lab {name} has no trace.sacct: there is nothing to replay (import one with the export, see docs/guides/config-lab.md)")
    nodes = lab_slurm.production_nodes(b["slurm"], b["topology"], b["gres"])
    load = lab_replay.plan_load(nodes, b["trace"], max_nodes or cfg["max_nodes"], cfg["max_running_jobs"], seed)
    sampled = load["sampled"]
    factor = lab_replay.time_factor(sampled, target)
    users = lab_replay.map_users(sampled, keep=False)  # decision 5: never the production names, whatever `get lab` was asked
    starts = [t for j in sampled if (t := lab_replay.parse_time(j.get("Submit", "")))]
    t0 = min(starts)
    jobs = [job_record(i, j, t0, factor, users) for i, j in enumerate(sampled, 1)]
    span = lab_replay.span_s(sampled)
    doc = {"contract": CONTRACT, "lab": name, "seed": seed, "target_hours": target, "factor": round(factor, 4),
           "length_s": round(span / factor), "lab_nodes": load["scale"]["lab_nodes"], "peak_running": load["peak"],
           "fits": load["fits"], "users": sorted(users.values()), "jobs": jobs}
    prod = production_users(name, root) | set(users)
    if n := leaks(doc, prod):
        raise LabError(f"refused: {n} production user name(s) also appear as an account, QOS, partition or TRES value in the trace; "
                       "rename them in the export (the jobs file must hold lab-uNNN names only, issue 554 decision 5)")
    return doc, prod


def dump(doc: Any) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(p)


# ---------------------------------------------------------------- the run

def reports_dir(name: str, root: Path | None = None) -> Path:
    return lab.path(name, root) / "reports"


def new_run_id(name: str, root: Path | None = None, now: Callable[[], float] = time.time) -> str:
    base = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now()))
    rid, n = base, 1
    while (reports_dir(name, root) / rid).exists():
        n += 1
        rid = f"{base}-{n}"
    return rid


class Run:
    """One run's record and progress, written to disk after every change so the governor thread (step 4) can read them."""

    def __init__(self, record: dict[str, Any], folder: Path, clock: Callable[[], float] = time.time, prod: set[str] | None = None):
        self.r, self.folder, self.clock = record, folder, clock
        self._prod = prod or set()  # the production user names, held in memory only (never written)

    # ---- creation and loading
    @classmethod
    def create(cls, name: str, sim: dict, root: Path | None = None, ab: bool = True, seed: int = DEFAULT_SEED,
               target_hours: float | None = None, max_nodes: int | None = None, clock: Callable[[], float] = time.time) -> Run:
        doc, prod = build_jobs(name, sim, root, seed, target_hours, max_nodes)
        rid = new_run_id(name, root, clock)
        folder = reports_dir(name, root) / rid
        text = dump(doc)
        folder.mkdir(parents=True)
        (folder / "jobs.json").write_text(text)
        (folder / "jobs.json").chmod(0o444)  # frozen: both sides read this file
        rec = {"contract": CONTRACT, "name": name, "run": rid, "ab": ab, "sides": ["a", "b"] if ab else ["b"],
               "seed": seed, "target_hours": doc["target_hours"], "factor": doc["factor"], "length_s": doc["length_s"],
               "jobs": len(doc["jobs"]), "jobs_sha256": sha256(text), "state": "idle", "history": [{"state": "idle", "at": clock()}],
               "paused_s": 0.0, "paused_side_s": dict.fromkeys(["a", "b"] if ab else ["b"], 0.0), "paused_since": None, "side": None, "counts": {"submitted": 0, "running": 0, "pending": 0},
               "replay_s": 0.0, "collected": {}, "snapshot": None, "restore": None, "reason": None}
        run = cls(rec, folder, clock, prod)
        run.save()
        return run

    @classmethod
    def load(cls, name: str, run_id: str, root: Path | None = None, clock: Callable[[], float] = time.time) -> Run:
        if not RUN_ID.fullmatch(run_id):
            raise LabError(f"run {run_id!r} is not a run id such as 20261117-120000")
        folder = reports_dir(name, root) / run_id
        try:
            rec = json.loads((folder / "run.json").read_text())
        except (OSError, ValueError) as e:
            raise LabError(f"no run {run_id} of lab {name}") from e
        return cls(rec, folder, clock, production_users(name, root))

    # ---- properties
    @property
    def path(self) -> tuple[str, ...]:
        return AB_PATH if self.r["ab"] else CANDIDATE_PATH

    @property
    def state(self) -> str:
        return self.r["state"]

    def jobs(self) -> dict[str, Any]:
        """The frozen jobs, refused if the file changed since the run was created."""
        text = (self.folder / "jobs.json").read_text()
        if sha256(text) != self.r["jobs_sha256"]:
            raise LabError(f"jobs.json of run {self.r['run']} changed after it was frozen; both sides must read the same jobs, start a new run")
        return json.loads(text)

    def comparable(self) -> bool:
        """Each side on its own: a side paused for more than 5 % of the run length (one side's length) is not comparable."""
        limit = COMPARABLE_PAUSE * max(1, self.r["length_s"])
        return all(self.paused_s(side) <= limit for side in self.r["sides"])

    def _ongoing(self) -> float:
        return self.clock() - self.r["paused_since"] if self.r["paused_since"] is not None else 0.0

    def paused_s(self, side: str | None = None) -> float:
        """Paused seconds of the whole run, or of one side, a pause in progress included."""
        if side is None:
            return round(self.r["paused_s"] + self._ongoing(), 1)
        return round(self.r["paused_side_s"].get(side, 0.0) + (self._ongoing() if self.r["side"] == side else 0.0), 1)

    def scrub(self, text: str) -> str:
        """Free text bound for run.json or progress.json (an abort reason, a tool's error) with every production user name replaced
        by `<user>`: a failing tool's message may name one (decision 5)."""
        return re.sub(r"[A-Za-z0-9_@-]+(?:\.[A-Za-z0-9_@-]+)*", lambda m: "<user>" if m.group(0) in self._prod else m.group(0), text)

    def last_state(self) -> str:
        """The state the run is in, or the one it had reached when it aborted."""
        return next(h["state"] for h in reversed(self.r["history"]) if h["state"] != "aborted")

    def percent(self) -> int:
        """Whole-run progress: each side's replay is an equal share, measured in lab time against the planned length."""
        if self.state == "done":
            return 100
        st, sides = self.last_state(), self.r["sides"]
        share, idx, done = 100 / len(sides), self.path.index(st), 0.0
        for i, s in enumerate(sides):
            if idx > self.path.index(f"replay_{s}"):
                done = share * (i + 1)
        if st in REPLAY:
            done = share * sides.index(REPLAY[st]) + share * min(1.0, self.r["replay_s"] / max(1, self.r["length_s"]))
        return min(99, int(done))

    def progress(self) -> dict[str, Any]:
        """ADR 0049's status block: {name, state, percent, submitted, running, pending, paused_s}, plus the run id, side and flags."""
        c = self.r["counts"]
        return {"name": self.r["name"], "state": self.state, "percent": self.percent(), "submitted": c["submitted"],
                "running": c["running"], "pending": c["pending"], "paused_s": self.paused_s(), "run": self.r["run"],
                "side": self.r["side"], "paused": self.r["paused_since"] is not None, "comparable": self.comparable(),
                "reason": self.r["reason"]}

    def save(self) -> None:
        write_atomic(self.folder / "run.json", dump(self.r))
        write_atomic(self.folder / "progress.json", dump(self.progress()))

    # ---- transitions
    def move(self, to: str, reason: str | None = None) -> None:
        if not legal(self.path, self.state, to):
            raise LabError(f"run {self.r['run']}: cannot go from {self.state} to {to}")
        if self.r["paused_since"] is not None:
            self.resume()
        self.r["state"] = to
        self.r["history"].append({"state": to, "at": self.clock()})
        if to in REPLAY:
            self.r.update(side=REPLAY[to], replay_s=0.0, counts={"submitted": 0, "running": 0, "pending": 0})
        if to == "aborted":
            self.r["reason"] = self.scrub(reason or "aborted")
        self.save()

    def abort(self, reason: str) -> None:
        if self.state not in TERMINAL:
            self.move("aborted", reason)

    def pause(self) -> None:
        """L2: the replay clock stops; the time is added to paused_s (over 5 % of the run length: not comparable)."""
        if self.state not in REPLAY:
            raise LabError(f"run {self.r['run']}: only a replay can pause, not {self.state}")
        if self.r["paused_since"] is None:
            self.r["paused_since"] = self.clock()
            self.save()

    def resume(self) -> None:
        if self.r["paused_since"] is not None:
            took = self._ongoing()
            self.r["paused_s"] = round(self.r["paused_s"] + took, 1)
            self.r["paused_side_s"][self.r["side"]] = round(self.r["paused_side_s"][self.r["side"]] + took, 1)
            self.r["paused_since"] = None
            self.save()

    def report_progress(self, submitted: int, running: int, pending: int, replay_s: float) -> None:
        """What the driver reports while it replays: counts and the lab time reached."""
        if self.state not in REPLAY:
            raise LabError(f"run {self.r['run']}: progress outside a replay ({self.state})")
        self.r["counts"] = {"submitted": int(submitted), "running": int(running), "pending": int(pending)}
        self.r["replay_s"] = float(replay_s)
        self.save()

    # ---- side folders and the collect-before-restore rule
    def side_dir(self, side: str) -> Path:
        if side not in SIDES:
            raise LabError(f"side must be a or b, not {side!r}")
        return self.folder / side

    def record_collected(self, side: str, files: dict[str, str]) -> None:
        """Store a side's accounting export (file name -> text) in the report folder after checking it holds no production user."""
        for fname, text in files.items():
            if n := leaks(text, self._prod):
                raise LabError(f"refused: the {SIDES[side]} accounting export holds {n} production user name(s); nothing was written")
            if "/" in fname or fname.startswith("."):
                raise LabError(f"bad export file name {fname!r}")
            write_atomic(self.side_dir(side) / fname, text)
        self.r["collected"][side] = sorted(files)
        self.save()

    def entered(self, state: str) -> float:
        """When the run last entered `state` (the clock's time)."""
        for h in reversed(self.r["history"]):
            if h["state"] == state:
                return h["at"]
        raise LabError(f"run {self.r['run']} has not been in {state}")

    def require_collected(self, side: str) -> None:
        """The restore erases the accounting of the side before it: it may run only once that side's export is on disk."""
        names = self.r["collected"].get(side) or []
        if not names or not all((self.side_dir(side) / n).is_file() for n in names):
            raise LabError(f"refused: run {SIDES[side]}'s accounting is not in the report folder yet; the restore would erase it")


# ---------------------------------------------------------------- A/B pairing

def parse_rows(text: str) -> list[dict[str, str]]:
    """A `sacct --parsable2` text with its header line as dicts."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    head = lines[0].split("|")
    return [dict(zip(head, ln.split("|"), strict=False)) for ln in lines[1:]]


def pair(a: list[dict[str, str]], b: list[dict[str, str]], key: str = "JobName") -> dict[str, Any]:
    """Pair the two sides' jobs by the jobs-file key the driver gives each job as its name (never by Slurm job id: the restore
    makes B reuse A's ids). Returns the pairs in key order and the keys only one side ran."""
    ia = {r.get(key, ""): r for r in a if r.get(key)}
    ib = {r.get(key, ""): r for r in b if r.get(key)}
    both = sorted(set(ia) & set(ib))
    return {"pairs": [(k, ia[k], ib[k]) for k in both], "only_a": sorted(set(ia) - set(ib)), "only_b": sorted(set(ib) - set(ia))}


def drive(run: Run, steps: Any) -> Run:
    """Walk the run's path with injected steps (tests use fakes; step 4's governor thread will pass the real ones):
    `snapshot()`, `apply(side_name)`, `replay(side, run)` (returns None or an abort reason), `collect(side, run) -> {file: text}`,
    `restore()`, `compare(run)`, `report(run)`, `abort_reason()` (None, or why to stop, checked before every state) and, optionally,
    `discard()` when the run has ended (`lab_snapshot.StateSteps` gives snapshot, restore, collect and discard).
    Any exception aborts the run with its message; the A/B path restores the snapshot if it was taken (cluster restored after abort)."""
    try:
        for to in run.path[run.path.index(run.state) + 1:]:
            if why := steps.abort_reason():
                raise LabError(why)
            run.move(to)
            if to == "snapshot":
                run.r["snapshot"] = steps.snapshot()
            elif to.startswith("apply_"):
                steps.apply(to.removeprefix("apply_"))
            elif to in REPLAY:
                if why := steps.replay(REPLAY[to], run):
                    raise LabError(why)
            elif to.startswith("collect_"):
                side = to.removeprefix("collect_")
                run.record_collected(side, steps.collect(side, run))
            elif to == "restore":
                run.require_collected("a")
                run.r["restore"] = steps.restore()
            elif to == "compare":
                steps.compare(run)
            elif to == "report":
                steps.report(run)
            run.save()
    except LabError as e:
        _abort(run, steps, str(e))
    except Exception as e:  # noqa: BLE001 - any failure ends the run in `aborted`, never half-way with a stale state
        _abort(run, steps, f"{type(e).__name__}: {e}")
    if run.state in TERMINAL and (discard := getattr(steps, "discard", None)):
        discard()  # the snapshot may hold production names in its accounting dump: it does not outlive the run
    return run


def _abort(run: Run, steps: Any, reason: str) -> None:
    """End the run as aborted. An A/B run whose snapshot was taken is put back to it (the cluster is restored after an abort);
    a side that was replaying or collecting is collected first, best effort, because the restore erases its accounting."""
    was = run.last_state()
    run.abort(reason)
    if not (run.r["ab"] and run.r.get("snapshot") is not None):
        return
    side = REPLAY.get(was) or (was.removeprefix("collect_") if was.startswith("collect_") else None)
    if side and side not in run.r["collected"]:
        try:
            run.record_collected(side, steps.collect(side, run))
        except Exception as e:  # noqa: BLE001 - partial accounting is a bonus; the restore still runs
            run.r["collect_error"] = run.scrub(str(e)[-300:])
    try:
        run.r["restore"] = steps.restore()
        run.r["restored_after_abort"] = True
    except Exception as e:  # noqa: BLE001 - the abort reason stays; the failed restore is recorded beside it
        run.r["restored_after_abort"] = False
        run.r["restore_error"] = run.scrub(str(e)[-300:])
    run.save()
