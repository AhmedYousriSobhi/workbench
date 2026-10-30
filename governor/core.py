"""Governor core: ties sampler, ladder, planner and the control commands together."""
import logging
import threading
from collections.abc import Callable

from . import metrics
from .annotate import Annotator
from .executors import UserExecutor
from .ladder import Ladder
from .planner import Action, Group, plan, reversible_first, undo
from .signals import Sampler, Signals
from .watchdog import Watchdog

log = logging.getLogger("governor")

Executor = Callable[[Action], None]
Effector = Callable[[list[str]], int]  # runs `simctl <args>`; returns the exit code

ADMIT_FLOOR = int(1.0 * 1024**3)  # MemAvailable that must remain after a start (matches benchctl preflight)
CHANGE_MAX_LEVEL = 1  # scale/set-profile fork docker; GV-8 forbids that under pressure (L2+)


class Governor:
    def __init__(self, sampler: Sampler, ladder: Ladder, groups: list[Group],
                 executor: Executor, dry_run: bool = True, watchdog: Watchdog | None = None,
                 watch_interval: float = 15.0, annotator: Annotator | None = None,
                 inventory: Callable[[], tuple[list[Group], dict]] | None = None,
                 refresh_interval: float = 30.0, effector: Effector | None = None):
        self.sampler, self.ladder, self.groups = sampler, ladder, groups
        self.executor, self.dry_run = executor, dry_run
        self.signals: Signals | None = None
        self.audit: list[str] = []
        self.counts = {"actions": 0, "action_failures": 0, "admit_ok": 0, "admit_refused": 0}
        self.metrics: bytes = b""  # rendered at the end of each step, served by governor.metrics
        self._lock = threading.Lock()
        self._prev = 0
        self.watchdog, self.watch_interval = watchdog, watch_interval
        self.violations: set[str] = set()
        self._next_watch = 0.0
        self._watch_failed = False
        self.annotator = annotator
        self.effector = effector
        self.inventory, self.refresh_interval = inventory, refresh_interval
        self._next_refresh = refresh_interval
        self._static = [g for g in groups if g.kind == "vm"]  # VMs are configured, not discovered
        self.stopped: set[str] = set()  # stopped by us: gone from `docker ps`, but an operator can still `start` them

    def group_names(self) -> set[str]:
        return {g.name for g in self.groups} | self.stopped

    def _act(self, a: Action) -> None:
        """Run or (dry-run) log one action, recording it in the audit trail (GV-6)."""
        msg = f"{'dry-run: would ' if self.dry_run else ''}{a}"
        log.info(msg)
        self._record(msg)
        self.counts["actions"] += 1
        if not self.dry_run:
            try:
                self.executor(a)
                if a.verb in ("stop", "save"):
                    self.stopped.add(a.target)
                elif a.verb == "start":
                    self.stopped.discard(a.target)
            except Exception as e:  # noqa: BLE001  one failed action must not abort the plan
                self.counts["action_failures"] += 1
                log.error("action failed: %s: %s", a, e)
                self._record(f"FAILED {a}: {e}")

    def step(self, now: float) -> int:
        """One control cycle: sample, update the ladder, act on level changes."""
        s = self.sampler.sample(now)
        with self._lock:
            self.signals = s
            level = self.ladder.update(s, now)
            if level != self._prev:
                self._record(f"level L{self._prev} -> L{level}")
                acts = undo(self._prev, level, self.groups) if level < self._prev else \
                    reversible_first(plan(level, self.groups))
                self._prev = level
                for a in acts:
                    self._act(a)
        self._watch(now, level)
        self._refresh(now, level)
        with self._lock:
            self.metrics = metrics.render(self)
        if self.annotator:
            (self.annotator.paused.set if level >= 3 else self.annotator.paused.clear)()
        return level

    def _refresh(self, now: float, level: int) -> None:
        """Re-discover containers so late starters are governed. Skipped from L3 up (forks docker)."""
        if not self.inventory or level >= 3 or now < self._next_refresh:
            return
        self._next_refresh = now + self.refresh_interval
        try:
            found, cgroups = self.inventory()
        except Exception as e:  # noqa: BLE001  discovery failure keeps the old inventory
            log.warning("inventory refresh failed: %s", e)
            return
        with self._lock:
            known = {g.name for g in self.groups}
            new = [g for g in found if g.name not in known]
            self.stopped -= {g.name for g in new}  # it is running again (started by someone else)
            gone = known - {g.name for g in found} - {g.name for g in self._static}
            if not new and not gone:
                return
            self.groups[:] = self._static + found
            if hasattr(self.executor, "refresh"):
                self.executor.refresh(self.groups, cgroups)
            for g in new:
                self._note(f"inventory: + {g.name} (P{g.prio})")
            for n in sorted(gone):
                self._note(f"inventory: - {n}")
            if level > 0:  # a container that appears mid-incident gets the current level's actions
                for a in reversible_first(plan(level, new)):
                    self._act(a)

    def _watch(self, now: float, level: int) -> None:
        """Report new/resolved security findings. Skipped from L3 up: it forks `docker`, which must
        not allocate while the host is starving (GV-8)."""
        if not self.watchdog or level >= 3 or now < self._next_watch:
            return
        self._next_watch = now + self.watch_interval
        try:
            found = set(self.watchdog.scan())
            self._watch_failed = False
        except Exception as e:  # noqa: BLE001  monitoring must never take the governor down
            if not self._watch_failed:
                self._note(f"watchdog unavailable: {e}")
            self._watch_failed = True
            return
        with self._lock:
            for v in sorted(found - self.violations):
                self._note(f"SECURITY: {v}", logging.WARNING)
            for v in sorted(self.violations - found):
                self._note(f"RESOLVED: {v}")
            self.violations = found

    def _note(self, msg: str, lvl: int = logging.INFO) -> None:
        log.log(lvl, msg)
        self._record(msg)

    def _record(self, msg: str) -> None:
        self.audit.append(msg)
        del self.audit[:-1000]
        if self.annotator:
            self.annotator.emit(msg)

    def _admit(self, budget_mb: int) -> dict:
        """GV-9: approve a start only when calm (below L2) and it fits in live MemAvailable."""
        if not self.signals:
            return {"ok": False, "error": "no signals yet"}
        level, avail = self.ladder.level, self.signals.mem_available
        left = avail - budget_mb * 1024**2
        if level >= 2:
            why = f"refused: governor at L{level}"
        elif left < ADMIT_FLOOR:
            why = f"refused: {budget_mb} MB would leave {left / 1024**3:.1f} GB (floor {ADMIT_FLOOR / 1024**3:.1f})"
        else:
            self.counts["admit_ok"] += 1
            self._record(f"admit {budget_mb} MB: ok")
            return {"ok": True}
        self.counts["admit_refused"] += 1
        self._record(f"admit {budget_mb} MB: {why}")
        return {"ok": False, "error": why}

    def _change(self, label: str, args: list[str]) -> dict:
        """Run a stack change (`simctl ...`) in the background; only calm (L0-L1) and with an effector."""
        if self.ladder.level > CHANGE_MAX_LEVEL:
            return {"ok": False, "error": f"refused at L{self.ladder.level}: stack changes are paused (GV-8)"}
        self._record(f"{'dry-run: would ' if self.dry_run else ''}{label}")
        if self.dry_run or not self.effector:
            return {"ok": True, "dry_run": True}

        def run() -> None:
            try:
                rc = self.effector(args)
            except Exception as e:  # noqa: BLE001
                self._record(f"FAILED {label}: {e}")
                return
            self._record(f"{label}: {'done' if rc == 0 else f'FAILED (exit {rc})'}")

        threading.Thread(target=run, daemon=True, name="change").start()
        return {"ok": True, "started": True}

    def handle(self, cmd: str, req: dict) -> dict:
        """Control-socket command handler; request already validated by control.dispatch."""
        with self._lock:
            if cmd == "status":
                s = self.signals
                return {"ok": True, "level": self.ladder.level, "dry_run": self.dry_run,
                        "mode": "user" if isinstance(self.executor, UserExecutor) else "root",
                        "mem_available": s.mem_available if s else None,
                        "psi_some": s.psi_some_avg10 if s else None,
                        "psi_full": s.psi_full_avg10 if s else None,
                        "groups": sorted(self.group_names()), "violations": sorted(self.violations),
                        "audit": self.audit[-20:]}
            if cmd in ("freeze", "thaw", "stop", "start"):
                self._act(Action(cmd, req["target"], self.ladder.level))
                return {"ok": True}
            if cmd == "admit":
                return self._admit(req["budget_mb"])
            if cmd == "scale":
                if req["group"] != "compute":
                    return {"ok": False, "error": "only group 'compute' can be scaled"}
                return self._change(f"scale={req['n']}", ["scale", str(req["n"])])
            if cmd == "set-profile":
                return self._change(f"set-profile={req['profile']}", ["up", "--profile", req["profile"]])
        return {"ok": False, "error": "unhandled"}
