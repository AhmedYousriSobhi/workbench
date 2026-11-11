"""One on-demand wire capture at a time (issue 505, ADR 0042, SPEC W-18).

The page's Focused wire panel asks for it; nothing runs otherwise. A capture is a `benchctl get capture NODE` subprocess (the governor
runs `benchctl`, like `slurm-stats`) whose JSON lines fill a small in-memory ring (500 messages). It is a class P5 workload: it starts
only at L0-L1 and stops when the ladder rises, after 5 minutes, when stopped, or when nobody has read it for 15 s (a closed tab).
Messages are never written to disk and are kept only until the next capture starts."""
import collections
import json
import threading
import time
from collections.abc import Callable

MAX_SECONDS = 300
RING = 500
IDLE_S = 15  # a capture the page stopped asking about ends (a closed tab must not leave a tcpdump running)
START_LEVEL_MAX = 1  # L0-L1 only; from L2 up the memory is wanted


class Capture:
    def __init__(self, spawn: Callable, level: Callable[[], int], record: Callable[[str], None], clock: Callable[[], float] = time.monotonic, tick: float = 1.0):
        self.spawn, self.level, self.record, self.clock, self.tick = spawn, level, record, clock, tick
        self.lock = threading.Lock()
        self.proc = None
        self.node: str | None = None
        self.ring: collections.deque = collections.deque(maxlen=RING)
        self.total, self.deadline, self.last_read = 0, 0.0, 0.0
        self.ended: str | None = None
        self.error: str | None = None
        self.gen = 0

    def running(self) -> bool:
        return self.proc is not None and self.ended is None

    def start(self, node: str) -> dict:
        with self.lock:
            if self.running():
                return {"ok": True, "node": self.node, "already": True} if node == self.node else {"ok": False, "error": f"a capture of {self.node} is running: one wire at a time"}
            if (lvl := self.level()) > START_LEVEL_MAX:
                return {"ok": False, "error": f"paused: memory pressure (L{lvl}); a capture starts only at L0-L1"}
            try:
                proc = self.spawn(node, MAX_SECONDS)
            except OSError as e:
                return {"ok": False, "error": f"capture could not start: {e}"}
            self.gen += 1
            self.proc, self.node, self.ended, self.error = proc, node, None, None
            self.ring.clear()
            self.total, self.deadline, self.last_read = 0, self.clock() + MAX_SECONDS, self.clock()
            gen = self.gen
        self.record(f"capture start {node} (class P5, L0-L1 only, at most {MAX_SECONDS // 60} min)")
        threading.Thread(target=self._read, args=(proc, gen), daemon=True, name="capture-read").start()
        threading.Thread(target=self._watch, args=(gen,), daemon=True, name="capture-watch").start()
        return {"ok": True, "node": node, "seconds": MAX_SECONDS}

    def _read(self, proc, gen: int) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                with self.lock:
                    if gen == self.gen:
                        self.ring.append(msg)
                        self.total += 1
        rc = proc.wait()
        err = ""
        if rc not in (0, -15) and proc.stderr is not None:
            try:
                err = proc.stderr.read().strip()[-200:]
            except (OSError, ValueError):
                err = ""
        with self.lock:
            if gen == self.gen and self.ended is None:
                self.ended = "time limit" if rc == 0 else "failed"
                self.error = None if rc == 0 else (err or f"exit {rc}")
        if rc not in (0, -15):
            self.record(f"capture of {self.node} failed: {err or f'exit {rc}'}")

    def _watch(self, gen: int) -> None:
        while True:
            time.sleep(self.tick)
            with self.lock:
                if gen != self.gen or not self.running():
                    return
                why = ("time limit" if self.clock() >= self.deadline else f"memory pressure (L{self.level()})" if self.level() > START_LEVEL_MAX
                       else "the page stopped asking" if self.clock() - self.last_read > IDLE_S else None)
            if why:
                self.stop(why)
                return

    def stop(self, reason: str = "stopped") -> dict:
        with self.lock:
            proc, node = self.proc, self.node
            if proc is None or self.ended is not None:
                return {"ok": True, "running": False}
            self.ended = reason
        try:
            proc.terminate()
        except OSError:
            pass
        self.record(f"capture stop {node}: {reason}")
        return {"ok": True, "running": False, "ended": reason}

    def read(self, since: int = 0) -> dict:
        """The messages after message number `since` (the `next` of the last read); asking also keeps the capture alive."""
        with self.lock:
            self.last_read = self.clock()
            first = self.total - len(self.ring)
            msgs = list(self.ring)[max(0, since - first):]
            left = max(0, round(self.deadline - self.clock())) if self.running() else 0
            return {"ok": True, "running": self.running(), "node": self.node, "messages": msgs, "next": self.total, "ended": self.ended, "error": self.error,
                    "seconds_left": left, "class": "P5"}
