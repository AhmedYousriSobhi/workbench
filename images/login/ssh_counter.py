#!/usr/bin/env python3
"""SSH session counter (issue 213, ADR 0088): counts the authenticated key logins sshd accepts, per source address, and serves the
totals as `GET /sim/stats` like the mocks do. Stdlib only; one file shared by the login and jump images (compose mounts it).

It reads sshd's own log file (`sshd -E FILE`, set in login.sh and the jump entrypoint), starting at the end, and repeats each new
line on its stdout so `docker logs` shows sshd as before. It never reads a command or a session's content: it keeps one number per
source address, taken from `Accepted publickey for USER from IP port N ssh2: TYPE ...` (the user name is dropped).
Not counted: a bare TCP connect (a liveness probe never authenticates), a refused login, and a Teleport login (its key type ends in
-CERT: the Teleport counter already has it; it is kept apart as `via_ca`). No `errors` field: sshd's refusals are several lines per
attempt, and a number that cannot be exact is not published."""
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ACCEPTED = re.compile(r"Accepted \S+ for \S+ from (\S+) port \d+ ssh2(?:: (\S+))?")
MAX_LOG = 4 << 20  # bytes: the log is truncated past this, so it never grows without end
MAX_SOURCES = 64  # distinct source addresses kept

LOCK = threading.Lock()
STATE = {"requests": 0, "via_ca": 0, "by_source": {}}


def feed(line: str) -> None:
    """Count one sshd log line."""
    m = ACCEPTED.search(line)
    if not m:
        return
    with LOCK:
        if (m.group(2) or "").endswith("-CERT"):
            STATE["via_ca"] += 1
            return
        STATE["requests"] += 1
        by = STATE["by_source"]
        if m.group(1) in by or len(by) < MAX_SOURCES:
            by[m.group(1)] = by.get(m.group(1), 0) + 1


def snapshot() -> dict:
    with LOCK:
        return {**STATE, "by_source": dict(STATE["by_source"])}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/sim/stats":
            body, code = json.dumps(snapshot()).encode(), 200
        else:
            body, code = b'{"error": "not found"}', 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silent: the page polls every second
        pass


def follow(path: str, out=sys.stdout, stop=lambda: False, wait: float = 0.2) -> None:
    """Tail `path` from its end, counting every new line and repeating it on `out`. Survives the file being absent, truncated or replaced."""
    f, ino, buf, first = None, None, "", True  # `first`: a file already there at start has old lines in it; one made later starts at 0
    while not stop():
        try:
            st = os.stat(path)
        except OSError:
            f, first = None, False
            time.sleep(wait)
            continue
        if f is None or st.st_ino != ino:
            if f:
                f.close()
            f, ino = open(path, "r", errors="replace"), st.st_ino  # noqa: SIM115 (held open between polls)
            if first:
                f.seek(0, os.SEEK_END)
            first = False
        elif st.st_size < f.tell():  # truncated: start over from the top
            f.seek(0)
        chunk = f.read()
        if not chunk:
            if st.st_size > MAX_LOG:
                os.truncate(path, 0)  # sshd appends (O_APPEND), so it carries on at the new end
            time.sleep(wait)
            continue
        buf += chunk
        *lines, buf = buf.split("\n")
        for line in lines:
            feed(line)
            print(line, file=out, flush=True)


def main(argv: list[str]) -> None:
    port = int(argv[1]) if len(argv) > 1 else 9922
    log = argv[2] if len(argv) > 2 else "/var/log/sshd.log"
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    follow(log)


if __name__ == "__main__":
    main(sys.argv)
