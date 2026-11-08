"""A fault proxy in front of the real Jira (issue 305): a real slow answer, a 503 or a 429 with a realistic Retry-After, which
Jira has no setting for. Standard library only, a few MB, runs on the host inside the scenario run (never in the Web UI, W-3).

The client points at 127.0.0.1:8084 instead of 127.0.0.1:8083 while the scenario runs. The failure body is the mock's
`POST /sim/failure` shape: {"mode": "503"|"429"|"slow", "count": N, "delay": S, "retry_after": S}; it applies to the next N
`/rest/` requests, then the proxy passes everything through again. `/status` and other paths are never failed.
"""
from __future__ import annotations

import http.client
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8084
UPSTREAM = ("127.0.0.1", 8083)
MODES = ("503", "429", "slow")
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization", "proxy-authenticate"}


class Fault:
    """The pending failure: thread safe, counts requests down."""

    def __init__(self, sleep=time.sleep):
        self.lock, self.sleep = threading.Lock(), sleep
        self.mode, self.count, self.delay, self.retry_after = None, 0, 2.0, 30

    def set(self, body: dict | None) -> None:
        body = body or {}
        mode = body.get("mode")
        if mode not in (None, *MODES):
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        with self.lock:
            self.mode, self.count = mode, int(body.get("count", 1)) if mode else 0
            self.delay, self.retry_after = float(body.get("delay", 2)), int(body.get("retry_after", 30))

    def take(self) -> tuple[str, float, int] | None:
        """One request's share of the failure, or None when there is none left."""
        with self.lock:
            if not self.mode or self.count <= 0:
                return None
            self.count -= 1
            return self.mode, self.delay, self.retry_after


def handler_for(fault: Fault, upstream: tuple[str, int]):
    class Proxy(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def reply(self, code: int, body: bytes = b"", headers: dict | None = None) -> None:
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def forward(self) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(n) if n else None
            if self.path.startswith("/rest/") and (hit := fault.take()):
                mode, delay, retry = hit
                if mode == "slow":
                    fault.sleep(delay)  # then the real answer, late
                elif mode == "429":
                    return self.reply(429, b'{"message":"Rate limit exceeded"}', {"Retry-After": str(retry), "Content-Type": "application/json"})
                else:
                    return self.reply(503, b'{"message":"Service temporarily unavailable"}', {"Retry-After": str(retry), "Content-Type": "application/json"})
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP | {"host", "content-length"}}
            try:
                conn = http.client.HTTPConnection(*upstream, timeout=60)
                conn.request(self.command, self.path, body=data, headers=headers)
                r = conn.getresponse()
                body = r.read()
                out = {k: v for k, v in r.getheaders() if k.lower() not in HOP | {"content-length"}}
                self.reply(r.status, body, out)
                conn.close()
            except OSError:
                self.reply(502, b'{"message":"the real Jira is not reachable"}', {"Content-Type": "application/json"})

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = forward

    return Proxy


class FaultProxy:
    def __init__(self, port: int = PORT, upstream: tuple[str, int] = UPSTREAM, sleep=time.sleep):
        self.fault = Fault(sleep)
        self.server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(self.fault, upstream))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def start(self) -> FaultProxy:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


_running: FaultProxy | None = None


def shared() -> FaultProxy:
    """The one proxy of this process (a scenario's steps share it); started on first use, ends with the process."""
    global _running
    if _running is None:
        _running = FaultProxy().start()
    return _running
