"""Governor core: ties sampler, ladder, planner and the control commands together."""
import logging
import threading
from collections.abc import Callable

from .ladder import Ladder
from .planner import Action, Group, plan, reversible_first, undo
from .signals import Sampler, Signals
from .watchdog import Watchdog

log = logging.getLogger("governor")

Executor = Callable[[Action], None]


class Governor:
    def __init__(self, sampler: Sampler, ladder: Ladder, groups: list[Group],
                 executor: Executor, dry_run: bool = True, watchdog: Watchdog | None = None,
                 watch_interval: float = 15.0):
        self.sampler, self.ladder, self.groups = sampler, ladder, groups
        self.executor, self.dry_run = executor, dry_run
        self.signals: Signals | None = None
        self.audit: list[str] = []
        self._lock = threading.Lock()
        self._prev = 0
        self.watchdog, self.watch_interval = watchdog, watch_interval
        self.violations: set[str] = set()
        self._next_watch = 0.0
        self._watch_failed = False

    def group_names(self) -> set[str]:
        return {g.name for g in self.groups}

    def _act(self, a: Action) -> None:
        """Run or (dry-run) log one action, recording it in the audit trail (GV-6)."""
        msg = f"{'dry-run: would ' if self.dry_run else ''}{a}"
        log.info(msg)
        self.audit.append(msg)
        del self.audit[:-1000]
        if not self.dry_run:
            try:
                self.executor(a)
            except Exception as e:  # noqa: BLE001  one failed action must not abort the plan
                log.error("action failed: %s: %s", a, e)
                self.audit.append(f"FAILED {a}: {e}")

    def step(self, now: float) -> int:
        """One control cycle: sample, update the ladder, act on level changes."""
        s = self.sampler.sample(now)
        with self._lock:
            self.signals = s
            level = self.ladder.update(s, now)
            if level != self._prev:
                acts = undo(self._prev, level, self.groups) if level < self._prev else \
                    reversible_first(plan(level, self.groups))
                self._prev = level
                for a in acts:
                    self._act(a)
        self._watch(now, level)
        return level

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
        self.audit.append(msg)
        del self.audit[:-1000]

    def handle(self, cmd: str, req: dict) -> dict:
        """Control-socket command handler; request already validated by control.dispatch."""
        with self._lock:
            if cmd == "status":
                s = self.signals
                return {"ok": True, "level": self.ladder.level, "dry_run": self.dry_run,
                        "mem_available": s.mem_available if s else None,
                        "psi_some": s.psi_some_avg10 if s else None,
                        "psi_full": s.psi_full_avg10 if s else None,
                        "groups": sorted(self.group_names()), "violations": sorted(self.violations),
                        "audit": self.audit[-20:]}
            if cmd in ("freeze", "thaw", "stop", "start"):
                self._act(Action(cmd, req["target"], self.ladder.level))
                return {"ok": True}
            if cmd == "scale":
                self._act(Action(f"scale={req['n']}", req["group"], self.ladder.level))
                return {"ok": True}
            if cmd == "set-profile":
                self._act(Action(f"set-profile={req['profile']}", "*", self.ladder.level))
                return {"ok": True}
        return {"ok": False, "error": "unhandled"}
