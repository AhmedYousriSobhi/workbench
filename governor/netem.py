"""In-cluster network faults with `tc netem`, only through the root governor service (issue 478, ADR 0056, SPEC C-12).

`tc` needs root, so only the root service runs it; the user-mode governor refuses with one message. The socket is group-writable (W-3),
so nothing free-form reaches a command line:

* Targets: a Slurm node container of this project only (name `workbench-NODE-1`, labels `workbench.node` and compose project `workbench`,
  never the host network). tc runs in the container's own network namespace (`nsenter -t PID -n`), on its `ethN` devices, so no host
  interface and no bridge is ever touched and the rule disappears with the container.
* Values: delay 1..MAX_DELAY_MS ms, loss 0.1..MAX_LOSS_PCT %, TTL 1..MAX_TTL_S s (always present, DEFAULT_TTL_S when not given).
* Set only at governor levels L0-L1 (`tc` forks); clearing is allowed at every level (it only removes load).
* Every rule is removed when the TTL ends, on `netem-clear`, at governor start and at governor exit, so a crashed scenario leaves nothing.
"""
import json
import re
import threading
import time
from collections.abc import Callable

MAX_DELAY_MS = 500
MAX_LOSS_PCT = 20.0
MIN_TTL_S, MAX_TTL_S, DEFAULT_TTL_S = 1, 600, 60
SET_LEVEL_MAX = 1  # L0-L1 only, like scale
PROJECT = "workbench"
DEV_RE = re.compile(r"eth[0-9]{1,2}")
_UNIT = {"ms": 1, "s": 1000}
_TTL = {"s": 1, "m": 60}
INSTALL_HINT = "sudo ./scripts/install-governor.sh"
USER_MODE_MSG = f"netem needs the root governor service (the user-mode governor refuses it): install it with {INSTALL_HINT}"


def parse_delay(text) -> int | None:
    """'200ms' (or '1s') -> milliseconds, None when the text is not a whole number of ms or s."""
    m = re.fullmatch(r"(\d{1,5})(ms|s)", str(text).strip())
    return int(m.group(1)) * _UNIT[m.group(2)] if m else None


def parse_loss(text) -> float | None:
    """'5%' or '0.5%' -> percent."""
    m = re.fullmatch(r"(\d{1,3}(?:\.\d{1,2})?)%", str(text).strip())
    return float(m.group(1)) if m else None


def parse_ttl(text) -> int | None:
    """'60s' or '5m' -> seconds."""
    m = re.fullmatch(r"(\d{1,5})([sm])", str(text).strip())
    return int(m.group(1)) * _TTL[m.group(2)] if m else None


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def problems(delay_ms, loss_pct, ttl_s) -> list[str]:
    """One message per condition; None means 'not given'. Used by the governor, the CLI and scenario validation."""
    out = []
    if delay_ms is None and loss_pct is None:
        out.append("netem needs a delay (such as 200ms) or a loss (such as 5%)")
    if delay_ms is not None and not (_num(delay_ms) and float(delay_ms).is_integer() and 1 <= delay_ms <= MAX_DELAY_MS):
        out.append(f"netem delay must be 1..{MAX_DELAY_MS} ms")
    if loss_pct is not None and not (_num(loss_pct) and 0.1 <= loss_pct <= MAX_LOSS_PCT):
        out.append(f"netem loss must be 0.1..{MAX_LOSS_PCT:g} %")
    if ttl_s is not None and not (_num(ttl_s) and float(ttl_s).is_integer() and MIN_TTL_S <= ttl_s <= MAX_TTL_S):
        out.append(f"netem ttl must be {MIN_TTL_S}..{MAX_TTL_S} s (every fault ends by itself)")
    return out


def netem_args(delay_ms, loss_pct) -> list[str]:
    """The `tc qdisc ... netem` words for validated values."""
    a = ["netem"]
    if delay_ms is not None:
        a += ["delay", f"{int(delay_ms)}ms"]
    if loss_pct is not None:
        a += ["loss", f"{float(loss_pct):g}%"]
    return a


class Netem:
    """`run(cmd) -> (returncode, stdout, stderr)` runs a host command (injected: tests never touch `tc`)."""

    def __init__(self, run: Callable[[list[str]], tuple[int, str, str]], level: Callable[[], int], record: Callable[[str], None],
                 clock: Callable[[], float] = time.monotonic, tick: float = 1.0):
        self.run, self.level, self.record, self.clock, self.tick = run, level, record, clock, tick
        self.lock = threading.RLock()
        self.active: dict[str, dict] = {}  # node -> {"deadline", "delay_ms", "loss_pct", "pid"}
        self._watching = False

    # --- targets -----------------------------------------------------------------------------------
    def _target(self, node: str) -> tuple[int | None, str | None]:
        """(pid, None) for a running Slurm node container of this project, else (None, why)."""
        name = f"{PROJECT}-{node}-1"
        rc, out, _ = self.run(["docker", "inspect", name])
        if rc:
            return None, f"no container {name}: netem targets this cluster's own Slurm nodes only"
        try:
            c = json.loads(out)[0]
            labels = c["Config"]["Labels"] or {}
            pid, running, mode = int(c["State"]["Pid"]), bool(c["State"]["Running"]), c["HostConfig"].get("NetworkMode", "")
        except (ValueError, KeyError, IndexError, TypeError):
            return None, f"cannot read {name} from docker inspect"
        if labels.get("com.docker.compose.project") != PROJECT or not labels.get("workbench.node") or mode == "host":
            return None, f"{name} is not a Slurm node of this cluster: netem targets those only"
        if not running or pid <= 0:
            return None, f"{name} is not running"
        return pid, None

    def _devices(self, pid: int) -> list[str]:
        rc, out, _ = self.run(["nsenter", "-t", str(pid), "-n", "ip", "-o", "link", "show"])
        if rc:
            return []
        devs = []
        for ln in out.splitlines():
            parts = ln.split(":")
            if len(parts) > 1:
                n = parts[1].strip().split("@")[0]
                if DEV_RE.fullmatch(n):
                    devs.append(n)
        return devs

    def _tc(self, pid: int, *a: str) -> tuple[int, str, str]:
        return self.run(["nsenter", "-t", str(pid), "-n", "tc", *a])

    def _clear_pid(self, pid: int) -> None:
        """Remove a root netem qdisc from every ethN of the namespace; leaves any other qdisc alone."""
        for d in self._devices(pid):
            rc, out, _ = self._tc(pid, "qdisc", "show", "dev", d)
            if rc == 0 and re.search(r"qdisc netem \S+ root", out):
                self._tc(pid, "qdisc", "del", "dev", d, "root")

    # --- commands ----------------------------------------------------------------------------------
    def set(self, node: str, delay_ms, loss_pct, ttl_s) -> dict:
        ttl_s = DEFAULT_TTL_S if ttl_s is None else int(ttl_s)
        if (lvl := self.level()) > SET_LEVEL_MAX:
            return {"ok": False, "error": f"paused: memory pressure (L{lvl}); netem starts only at L0-L1"}
        with self.lock:
            pid, why = self._target(node)
            if why:
                return {"ok": False, "error": why}
            devs = self._devices(pid)
            if not devs:
                return {"ok": False, "error": f"no ethN device in {node}'s network namespace"}
            for d in devs:
                rc, _, err = self._tc(pid, "qdisc", "replace", "dev", d, "root", *netem_args(delay_ms, loss_pct))
                if rc:
                    self._clear_pid(pid)
                    return {"ok": False, "error": f"tc failed on {node} {d}: {(err or '').strip()[:150] or f'exit {rc}'}"}
            self.active[node] = {"deadline": self.clock() + ttl_s, "delay_ms": delay_ms, "loss_pct": loss_pct, "pid": pid}
            self._ensure_watch()
        self.record(f"netem set {node} {' '.join(netem_args(delay_ms, loss_pct)[1:])} for {ttl_s}s (root service, L0-L1 only)")
        return {"ok": True, "node": node, "ttl_s": ttl_s}

    def clear(self, node: str | None = None, reason: str = "cleared") -> dict:
        with self.lock:
            nodes = [node] if node else list(self.active)
            for n in nodes:
                entry = self.active.pop(n, None)
                pid = entry["pid"] if entry else self._target(n)[0]
                if pid:
                    self._clear_pid(pid)
                if entry:
                    self.record(f"netem clear {n}: {reason}")
        return {"ok": True, "cleared": nodes}

    def clear_all(self, reason: str = "governor start") -> int:
        """Every running Slurm node of this project loses any netem rule (startup and exit). Returns how many containers were looked at."""
        rc, out, _ = self.run(["docker", "ps", "-q", "--filter", "label=workbench.node", "--filter", f"label=com.docker.compose.project={PROJECT}"])
        ids = out.split() if rc == 0 else []
        n = 0
        for cid in ids:
            rc, o, _ = self.run(["docker", "inspect", "-f", "{{.State.Pid}}", cid])
            if rc == 0 and o.strip().isdigit() and int(o) > 0:
                self._clear_pid(int(o))
                n += 1
        with self.lock:
            self.active.clear()
        self.record(f"netem rules cleared on {n} node containers ({reason})")
        return n

    def status(self) -> list[dict]:
        now = self.clock()
        with self.lock:
            return [{"node": n, "delay_ms": e["delay_ms"], "loss_pct": e["loss_pct"], "ends_in_s": max(0, round(e["deadline"] - now))}
                    for n, e in sorted(self.active.items())]

    # --- TTL ---------------------------------------------------------------------------------------
    def _ensure_watch(self) -> None:
        if not self._watching:
            self._watching = True
            threading.Thread(target=self._watch, daemon=True, name="netem-ttl").start()

    def expire(self) -> list[str]:
        """Clear every fault whose TTL has ended; returns the nodes."""
        with self.lock:
            due = [n for n, e in self.active.items() if e["deadline"] <= self.clock()]
        for n in due:
            self.clear(n, "ttl ended")
        return due

    def _watch(self) -> None:
        while True:
            time.sleep(self.tick)
            self.expire()
            with self.lock:
                if not self.active:
                    self._watching = False
                    return
