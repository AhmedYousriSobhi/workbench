"""Governor core: ties sampler, ladder, planner and the control commands together."""
import logging
import threading
from collections.abc import Callable

from .ladder import Ladder
from .planner import Action, Group, plan, reversible_first
from .signals import Sampler, Signals

log = logging.getLogger("governor")

Executor = Callable[[Action], None]


class Governor:
    def __init__(self, sampler: Sampler, ladder: Ladder, groups: list[Group],
                 executor: Executor, dry_run: bool = True):
        self.sampler, self.ladder, self.groups = sampler, ladder, groups
        self.executor, self.dry_run = executor, dry_run
        self.signals: Signals | None = None
        self.audit: list[str] = []
        self._lock = threading.Lock()
        self._prev = 0

    def group_names(self) -> set[str]:
        return {g.name for g in self.groups}

    def _act(self, a: Action) -> None:
        """Run or (dry-run) log one action, recording it in the audit trail (GV-6)."""
        msg = f"{'dry-run: would ' if self.dry_run else ''}{a}"
        log.info(msg)
        self.audit.append(msg)
        del self.audit[:-1000]
        if not self.dry_run:
            self.executor(a)

    def step(self, now: float) -> int:
        """One control cycle: sample, update the ladder, act on level changes."""
        s = self.sampler.sample(now)
        with self._lock:
            self.signals = s
            level = self.ladder.update(s, now)
            if level != self._prev:
                self._prev = level
                for a in reversible_first(plan(level, self.groups)):
                    self._act(a)
        return level

    def handle(self, cmd: str, req: dict) -> dict:
        """Control-socket command handler; request already validated by control.dispatch."""
        with self._lock:
            if cmd == "status":
                s = self.signals
                return {"ok": True, "level": self.ladder.level, "dry_run": self.dry_run,
                        "mem_available": s.mem_available if s else None,
                        "psi_some": s.psi_some_avg10 if s else None,
                        "psi_full": s.psi_full_avg10 if s else None,
                        "groups": sorted(self.group_names()), "audit": self.audit[-20:]}
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
