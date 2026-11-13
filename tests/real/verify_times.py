"""Per-module wall time of a live run (issue 576, V-7): the cost is in module fixtures (stack up and down), which --durations does not show.
Pure bookkeeping, no pytest import, so a unit test can load it."""
import json
import time
from pathlib import Path

KEEP_RUNS = 20


class Recorder:
    def __init__(self, isolated: set[str], clock=time.monotonic):
        self.isolated, self.clock = isolated, clock
        self.reuse: set[str] = set()  # STACK_REUSE modules: isolated by configuration, they share a stack with the same key (issue 576)
        self.mods: dict[str, dict] = {}

    def start(self, module: str) -> None:
        now = self.clock()
        self.mods.setdefault(module, {"t0": now, "t1": now, "tests": 0, "failed": 0, "cold": module in self.isolated and module not in self.reuse, "reused": module in self.reuse})

    def finish(self, module: str) -> None:
        """Called after each test's teardown; the last call of a module also covers its module-fixture teardown."""
        if module in self.mods:
            self.mods[module]["t1"] = self.clock()
            self.mods[module]["tests"] += 1

    def outcome(self, module: str, failed: bool) -> None:
        if failed and module in self.mods:
            self.mods[module]["failed"] += 1

    def rows(self) -> list[dict]:
        out = [{"module": m, "seconds": round(v["t1"] - v["t0"], 1), "cold_start": v["cold"], "reused_stack": v["reused"], "tests": v["tests"], "failed": v["failed"]} for m, v in self.mods.items()]
        return sorted(out, key=lambda r: -r["seconds"])

    def slow_line(self, n: int = 10) -> str:
        rows = self.rows()[:n]
        return "SLOW  " + " · ".join(f"{r['module']} {r['seconds']:.0f}s ({'cold' if r['cold_start'] else 'reused' if r['reused_stack'] else 'shared'})" for r in rows) if rows else ""

    def write(self, path: Path, stamp: str) -> None:
        """Append this run, keep the last KEEP_RUNS. An unreadable file starts over."""
        try:
            runs = json.loads(path.read_text())
            runs = runs if isinstance(runs, list) else []
        except (OSError, ValueError):
            runs = []
        runs.append({"at": stamp, "modules": self.rows()})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(runs[-KEEP_RUNS:], indent=1))
