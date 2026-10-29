"""Control socket: JSON-lines over a Unix socket with a fixed allow-list (SPEC 4a, W-3)."""
import json
import logging
import os
import socketserver
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("governor.control")

ALLOWED = {"status", "scale", "freeze", "thaw", "stop", "start", "set-profile", "admit"}
MAX_REQUEST = 4096
TARGETED = {"freeze", "thaw", "stop", "start"}  # require a known group name

Handler = Callable[[str, dict], dict]


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
