"""Maps a ladder level to ordered actions over priority-classed groups (GV-5)."""
from dataclasses import dataclass

# action name -> reversible?
REVERSIBLE = {"notify": True, "stop-arrivals": True, "throttle": True, "freeze": True,
              "reclaim": True, "stop": False, "save": False,
              "thaw": True, "unthrottle": True, "resume-arrivals": True}


@dataclass(frozen=True)
class Group:
    name: str
    prio: int  # 0..6, higher number = lower priority = acted on first
    kind: str = "container"  # or "vm"


@dataclass(frozen=True)
class Action:
    verb: str
    target: str
    level: int

    def __str__(self) -> str:
        return f"L{self.level} {self.verb} {self.target}"


def _by_prio(groups: list[Group], prios: set[int]) -> list[Group]:
    return sorted((g for g in groups if g.prio in prios), key=lambda g: (-g.prio, g.name))


def plan(level: int, groups: list[Group]) -> list[Action]:
    """Cumulative plan for `level`: everything a lower level does still applies."""
    acts: list[Action] = []
    if level >= 1:
        acts.append(Action("notify", "events", 1))
        acts.append(Action("stop-arrivals", "*", 1))  # global flag read by user-sim/ticket-bot
    if level >= 2:
        acts += [Action("throttle", g.name, 2) for g in _by_prio(groups, {5, 4})]
    if level >= 3:
        for g in _by_prio(groups, {6, 5, 4}):
            acts.append(Action("freeze", g.name, 3))
        for g in _by_prio(groups, {6, 5, 4}):
            acts.append(Action("reclaim", g.name, 3))
    if level >= 4:
        for g in _by_prio(groups, {6, 5, 4, 3}):
            acts.append(Action("save" if g.kind == "vm" else "stop", g.name, 4))
    if level >= 5:
        for g in _by_prio(groups, {2, 3, 4, 5, 6}):
            a = Action("save" if g.kind == "vm" else "stop", g.name, 5)
            if a.target not in {x.target for x in acts if x.verb in ("stop", "save")}:
                acts.append(a)
    return acts


def reversible_first(acts: list[Action]) -> list[Action]:
    """Stable sort: reversible actions precede destructive ones."""
    return sorted(acts, key=lambda a: not REVERSIBLE[a.verb])


def undo(old: int, new: int, groups: list[Group]) -> list[Action]:
    """Reverse reversible actions whose level is above `new` (stops are never auto-undone)."""
    if new >= old:
        return []
    inverse = {"freeze": "thaw", "throttle": "unthrottle", "stop-arrivals": "resume-arrivals"}
    seen: set[tuple[str, str]] = set()
    out: list[Action] = []
    for a in plan(old, groups):
        if a.verb in inverse and a.level > new and (a.verb, a.target) not in seen:
            seen.add((a.verb, a.target))
            # reverse in the opposite order: highest priority first
            out.append(Action(inverse[a.verb], a.target, new))
    return out[::-1]
