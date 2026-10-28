"""Escalation ladder L0-L5 with hysteresis (GV-3, GV-4). Pure logic, no I/O."""
from dataclasses import dataclass, field

from .signals import GB, Signals


@dataclass
class Thresholds:
    """README 6.3 starting values; override from config."""

    l0_psi: float = 5.0
    l0_mem: int = int(2.5 * GB)
    l1_psi: float = 10.0
    l1_mem: int = int(2.0 * GB)
    l2_psi: float = 20.0
    l3_psi: float = 30.0
    sustain_s: float = 10.0  # L2/L3 PSI must hold this long
    l4_full: float = 5.0
    l4_mem: int = int(1.0 * GB)
    l5_after_s: float = 30.0  # L4 condition persisting this long -> L5
    calm_s: float = 60.0  # calm needed per step down
    min_dwell_s: float = 10.0


@dataclass
class Ladder:
    t: Thresholds = field(default_factory=Thresholds)
    level: int = 0
    _since: dict[str, float] = field(default_factory=dict)  # condition -> first-true time
    _entered: float = 0.0
    _calm_from: float | None = None

    def _held(self, name: str, cond: bool, now: float) -> float:
        """Seconds the condition has been continuously true (0 if false)."""
        if not cond:
            self._since.pop(name, None)
            return 0.0
        return now - self._since.setdefault(name, now)

    def target(self, s: Signals, now: float) -> int:
        t = self.t
        l4 = s.psi_full_avg10 > t.l4_full or s.mem_available < t.l4_mem
        if self._held("l4", l4, now) >= t.l5_after_s:
            return 5
        if l4:
            return 4
        if self._held("l3", s.psi_some_avg10 > t.l3_psi, now) >= t.sustain_s:
            return 3
        if self._held("l2", s.psi_some_avg10 > t.l2_psi, now) >= t.sustain_s:
            return 2
        if s.psi_some_avg10 > t.l1_psi or s.mem_available < t.l1_mem:
            return 1
        return 0

    def calm(self, s: Signals) -> bool:
        return s.psi_some_avg10 < self.t.l0_psi and s.mem_available > self.t.l0_mem

    def update(self, s: Signals, now: float) -> int:
        """Advance the ladder; returns the (possibly new) level."""
        tgt = self.target(s, now)
        if tgt > self.level:  # escalate immediately
            self._move(tgt, now)
        elif tgt < self.level:
            # level 1 relaxes on the same condition as the target; deeper levels step down
            # one at a time after calm_s continuously below the current level
            if self._calm_from is None:
                self._calm_from = now
            if (
                now - self._calm_from >= self.t.calm_s
                and now - self._entered >= self.t.min_dwell_s
            ):
                self._move(self.level - 1, now)
        else:
            self._calm_from = None
        return self.level

    def _move(self, level: int, now: float) -> None:
        down = level < self.level
        self.level = level
        self._entered = now
        self._calm_from = now if down else None  # each step down needs its own calm_s
