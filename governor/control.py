"""Control socket: JSON-lines over a Unix socket with a fixed allow-list (SPEC 4a, W-3)."""
import json
import logging
import os
import re
import socketserver
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("governor.control")

ALLOWED = {"status", "scale", "freeze", "thaw", "stop", "start", "set-profile", "admit", "apply-plan", "node-state", "submit-job"}
MAX_REQUEST = 4096
TARGETED = {"freeze", "thaw", "stop", "start"}  # require a known group name

Handler = Callable[[str, dict], dict]


NODE_RE = re.compile(r"[a-z]{2,8}[0-9]{3}")  # sim.yaml node names: cn001, gpu002
REASON_RE = re.compile(r"[A-Za-z0-9 .,:_/()+-]{0,60}")  # a drain reason: short, no shell characters
LOGIN_MODES = {"ssh", "teleport", "both"}  # mirrors benchctl.logins.MODES
PLAN_PARTS = {"jira", "bmc", "bcm", "scenarios", "accounting", "load", "teleport"}  # mirrors benchctl.workbench.PARTS


def plan_error(req: dict) -> str | None:
    """Only small integers and known part names reach the command line; nothing free-form."""
    for key, lo, hi in (("cpu_nodes", 0, 64), ("gpu_nodes", 0, 16), ("gpus_per_node", 1, 8)):
        v = req.get(key)
        if not (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi):
            return f"apply-plan needs {key}: int {lo}..{hi}"
    parts = req.get("parts")
    if not (isinstance(parts, list) and all(isinstance(p, str) and p in PLAN_PARTS for p in parts)):
        return f"apply-plan parts must be a list of: {', '.join(sorted(PLAN_PARTS))}"
    nodes = req.get("bmc_nodes")  # optional: the nodes that get a BMC; absent means every node
    if nodes is not None and not (isinstance(nodes, list) and len(nodes) <= 80
                                  and all(isinstance(n, str) and NODE_RE.fullmatch(n) for n in nodes)):
        return "apply-plan bmc_nodes must be a list of node names such as cn001"
    modes = req.get("login_nodes")  # optional: one auth mode per login node; absent keeps sim.yaml's
    if modes is not None and not (isinstance(modes, list) and 1 <= len(modes) <= 8 and all(m in LOGIN_MODES for m in modes)):
        return f"apply-plan login_nodes must be 1 to 8 of: {', '.join(sorted(LOGIN_MODES))}"
    return None


def dispatch(line: bytes, handle: Handler, groups: Callable[[], set[str]]) -> dict:
    """Validate one request and route it. Never raises."""
    try:
        if len(line) > MAX_REQUEST:
            return {"ok": False, "error": "request too large"}
        req = json.loads(line)
        if not isinstance(req, dict):
            return {"ok": False, "error": "request must be an object"}
        cmd = req.get("cmd")
        if cmd not in ALLOWED:
            return {"ok": False, "error": f"unknown command: {cmd!r}"}
        if cmd in TARGETED and req.get("target") not in groups():
            return {"ok": False, "error": f"unknown group: {req.get('target')!r}"}
        if cmd == "scale" and not (
            isinstance(req.get("group"), str) and isinstance(req.get("n"), int) and req["n"] >= 0
        ):
            return {"ok": False, "error": "scale needs group:str and n:int>=0"}
        if cmd == "set-profile" and not isinstance(req.get("profile"), str):
            return {"ok": False, "error": "set-profile needs profile:str"}
        if cmd == "apply-plan" and (err := plan_error(req)):
            return {"ok": False, "error": err}
        if cmd == "node-state" and not (isinstance(req.get("node"), str) and NODE_RE.fullmatch(req["node"])
                                        and req.get("state") in ("off", "on", "down", "resume", "drain")
                                        and REASON_RE.fullmatch(req.get("reason") or "")):
            return {"ok": False, "error": "node-state needs node:NAME (cn001), state: off|on|down|resume|drain and an optional short reason"}
        if cmd == "submit-job" and not (isinstance(req.get("node"), str) and NODE_RE.fullmatch(req["node"])
                                        and all(isinstance(req.get(k), int) and not isinstance(req.get(k), bool) and lo <= req[k] <= hi
                                                for k, lo, hi in (("gpus", 0, 8), ("duration", 10, 3600)))
                                        and re.fullmatch(r"[a-z][a-z0-9_-]{0,15}", req.get("user") or "x")):
            return {"ok": False, "error": "submit-job needs node:NAME, gpus:int 0..8, duration:int 10..3600 and an optional user"}
        if cmd == "admit" and not (isinstance(req.get("budget_mb"), int) and req["budget_mb"] >= 0):
            return {"ok": False, "error": "admit needs budget_mb:int>=0"}
        return handle(cmd, req)
    except (ValueError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"bad json: {e}"}
    except Exception as e:  # keep the governor alive whatever a client sends
        log.exception("handler failed")
        return {"ok": False, "error": f"internal: {e}"}


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(path: str, handle: Handler, groups: Callable[[], set[str]],
          mode: int = 0o660, gid: int | None = None) -> socketserver.UnixStreamServer:
    """Start the control server in a daemon thread and return it (call .shutdown() to stop)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.unlink()

    class H(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.request.settimeout(5)
            try:
                line = self.rfile.readline(MAX_REQUEST + 2)
            except OSError:
                return
            if not line.strip():
                return
            reply = dispatch(line, handle_cmd, groups)
            self.wfile.write(json.dumps(reply).encode() + b"\n")

    handle_cmd = handle
    srv = _Server(path, H)
    os.chmod(path, mode)
    if gid is not None:
        os.chown(path, -1, gid)
        # the group must also traverse the directory holding the socket
        os.chown(p.parent, -1, gid)
        os.chmod(p.parent, 0o750)
    threading.Thread(target=srv.serve_forever, daemon=True, name="control").start()
    return srv
