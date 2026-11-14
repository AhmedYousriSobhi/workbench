"""Fake BCM CMDaemon: the JSON API subset the app reads, one process, nodes from the generated inventory.json.

POST /json  {"service": "cmdevice", "call": "getDevices"}
            {"service": "cmdevice", "call": "getDevice", "args": ["cn001"]}
            {"service": "cmdevice", "call": "powerOn|powerOff|powerReset", "args": ["cn001"]}
            {"service": "cmstatus", "call": "getStatus", "args": ["cn001"]}
            {"service": "cmpart", "call": "getCategories"}
GET  /healthz
POST /sim/inject  {"node": n, "state": "UP|DOWN|CLOSED|INSTALLING"} | {"node": n, "closed": true}

The call names follow CMDaemon's service/call convention but are an assumed subset (ADR 0005); extend CALLS.
"""
import collections
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATES = ("UP", "DOWN", "CLOSED", "INSTALLING")
POWER_TO_STATE = {"powerOn": "UP", "powerOff": "DOWN", "powerReset": "UP"}


def load_nodes(inventory: Path) -> dict[str, dict]:
    """{node: {"category": c, "gpus": n, "memory_mb": m, "cpus": c}} from the generated inventory.json (benchctl/hwinventory.py), in its order.
    Kubernetes nodes are listed only when BCM manages them (`bcm_manages_kubernetes`, issue 413)."""
    inv = json.loads(Path(inventory).read_text())
    return {n["name"]: {"category": n.get("category", n["kind"]), "ip": n.get("ip"), "gpus": n["gpus"], "memory_mb": n["memory_mb"], "cpus": n["cpus"]}
            for n in inv["nodes"] if inv.get("bcm_manages_kubernetes", True) or not n["kind"].startswith("k8s")}


class Cluster:
    def __init__(self, nodes: dict[str, dict]):
        self.nodes = nodes
        self.state = {n: "UP" for n in nodes}

    def device(self, name: str) -> dict:
        n = self.nodes[name]
        return {"hostname": name, "childType": "PhysicalNode", "category": n["category"], "ip": self.ip(name),
                "gpus": n["gpus"], "memory_mb": n["memory_mb"], "cpus": n["cpus"], "status": self.state[name]}

    def ip(self, name: str) -> str:
        if self.nodes[name].get("ip"):  # a node with its own fixed address (the real card's node, issue 431)
            return self.nodes[name]["ip"]
        kind = self.nodes[name]["category"]  # the same formula as benchctl/addressing.py host_ip (this file cannot import it)
        i = [n for n, v in self.nodes.items() if v["category"] == kind and not v.get("ip")].index(name)
        return f"10.141.{i // 254 + {'gpu': 100, 'k8s-cpu': 200, 'k8s-gpu': 230}.get(kind, 0)}.{i % 254 + 1}"  # benchctl/addressing.py BLOCK

    def inject(self, req: dict) -> None:
        node = req["node"]
        if node not in self.nodes:
            raise KeyError(node)
        state = "CLOSED" if req.get("closed") else req.get("state")
        if state not in STATES:
            raise ValueError(f"state must be one of {STATES}")
        self.state[node] = state

    def call(self, req: dict):
        """(result, error); error is a CMDaemon-like message or None."""
        service, call, args = req.get("service"), req.get("call"), req.get("args") or []
        if (service, call) == ("cmdevice", "getDevices"):
            return [self.device(n) for n in self.nodes], None
        if (service, call) == ("cmpart", "getCategories"):
            return [{"name": c} for c in sorted({n["category"] for n in self.nodes.values()})], None
        if service in ("cmdevice", "cmstatus") and args:
            name = args[0]
            if name not in self.nodes:
                return None, f"Unknown device: {name}"
            if (service, call) == ("cmdevice", "getDevice"):
                return self.device(name), None
            if (service, call) == ("cmstatus", "getStatus"):
                return {"hostname": name, "state": self.state[name]}, None
            if service == "cmdevice" and call in POWER_TO_STATE:
                self.state[name] = POWER_TO_STATE[call]
                return {"hostname": name, "state": self.state[name]}, None
        return None, f"Unknown call: {service}.{call}"


PROBE = "x-workbench-probe"  # the web UI sets this on its own polls so they are not drawn as client traffic


def counted(path: str, headers) -> bool:
    """A real client request: not a health check, not /sim control, not a UI probe."""
    return not (path.startswith(("/healthz", "/sim/")) or headers.get(PROBE))


# --- request shapes (issue 475): which paths a client calls and which 404. Method and path shape only: a segment with a digit
# (an id, an issue key, a node name; not a version or a 1-2 digit number) becomes {id}, query values are dropped, nothing from a body or header is kept. The three
# mocks carry the same block (tests/test_path_shapes.py checks they match). GET /sim/paths shows it; GET /sim/traffic[?since=T] adds who asked
# (the User-Agent product token), the status class and the latency of each stream, and the last 400 requests (issue 501).
SHAPE_CAP = 300  # distinct shapes kept; the rest count as "other"


def shape(path: str) -> str:
    base, _, query = path.partition("?")
    segs = ["{id}" if re.search(r"\d", s) and not re.fullmatch(r"v?\d{1,2}", s) else s for s in base.split("/")]
    keys = sorted({kv.split("=")[0] for kv in query.split("&") if kv})[:8]
    return "/".join(segs)[:120] + ("?" + "&".join(k[:30] for k in keys) if keys else "")


RING_CAP = 400  # recent requests kept; the oldest fall off
HIST_MS = (5, 10, 25, 50, 100, 250, 500, 1000)  # latency histogram: upper edges in ms, one more bucket above the last


def caller(headers) -> str:
    """Who asked, as the User-Agent's product token only (no version, no other header): curl, python-requests, ... at most 24 characters."""
    m = re.match(r"[A-Za-z][A-Za-z0-9_.-]{0,23}", headers.get("User-Agent") or "")
    return m[0].lower() if m else "unknown"


def elapsed_ms(handler) -> float:
    t0 = getattr(handler, "_t0", None)
    return 0.0 if t0 is None else (time.monotonic() - t0) * 1000


def since_of(path: str) -> float:
    m = re.search(r"[?&]since=([0-9.]+)", path)
    return float(m[1]) if m else 0.0


class Shapes:
    def __init__(self) -> None:
        self.lock, self.paths, self.unknown, self.streams = threading.Lock(), {}, {}, {}
        self.ring = collections.deque(maxlen=RING_CAP)

    def reset(self) -> None:
        with self.lock:
            self.paths, self.unknown, self.streams = {}, {}, {}
            self.ring.clear()

    def add(self, method: str, path: str, code: int, label: str | None = None, unknown: bool | None = None,
            ms: float = 0.0, who: str = "unknown", now: float | None = None) -> None:
        key = f"{method} {label or shape(path)}"
        with self.lock:
            folded = key
            for table, hit in ((self.paths, True), (self.unknown, code == 404 if unknown is None else unknown)):
                if hit:
                    k = key if key in table or len(table) < SHAPE_CAP else "other"
                    table[k] = table.get(k, 0) + 1
                    if table is self.paths:
                        folded = k
            sk = (who, folded) if (who, folded) in self.streams or len(self.streams) < SHAPE_CAP else (who, "other")
            row = self.streams.setdefault(sk, [0, 0, 0, 0, 0.0, [0] * (len(HIST_MS) + 1)])
            row[0] += 1
            row[1 + (0 if code < 400 else 1 if code < 500 else 2)] += 1
            row[4] += ms
            row[5][sum(1 for e in HIST_MS if ms > e)] += 1
            self.ring.append((round(time.time() if now is None else now, 3), who, folded, code, round(ms, 1)))

    def snapshot(self) -> dict:
        with self.lock:
            return {"paths": dict(sorted(self.paths.items())), "unknown": dict(sorted(self.unknown.items()))}

    def traffic(self, since: float = 0.0) -> dict:
        """Streams (who called what: counts by status class and a latency histogram) and the recent requests after `since`."""
        with self.lock:
            return {"now": round(time.time(), 3), "edges": list(HIST_MS),
                    "streams": [{"who": w, "shape": k, "n": r[0], "ok": r[1], "client_error": r[2], "server_error": r[3], "ms": round(r[4], 1), "hist": list(r[5])}
                                for (w, k), r in sorted(self.streams.items())],
                    "recent": [list(x) for x in self.ring if x[0] > since]}


SHAPES = Shapes()


def make_handler(cluster: Cluster):
    stats, lock = {"requests": 0, "errors": 0}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def parse_request(self):  # the clock for the latency of this request starts here, not at the keep-alive wait before it
            ok = super().parse_request()
            self._t0 = time.monotonic()
            return ok

        label = is_unknown = None

        def _send(self, code: int, obj) -> None:
            if counted(self.path, self.headers):
                SHAPES.add(self.command, self.path, code, getattr(self, "label", None), getattr(self, "is_unknown", None), ms=elapsed_ms(self), who=caller(self.headers))
                with lock:
                    stats["requests"] += 1
                    if code >= 400:  # an error answer (401, 404, 503 ...): the page draws it as a different dot (#314)
                        stats["errors"] += 1
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/healthz":
                return self._send(200, {"nodes": len(cluster.nodes)})
            if self.path == "/sim/stats":
                return self._send(200, dict(stats))
            if self.path == "/sim/paths":
                return self._send(200, SHAPES.snapshot())
            if self.path.split("?")[0] == "/sim/traffic":
                return self._send(200, SHAPES.traffic(since_of(self.path)))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            if self.path == "/json":
                result, err = cluster.call(req)
                self.label = "/json " + re.sub(r"[^\w.]", "", f"{req.get('service')}.{req.get('call')}")[:60]  # the call, never its args
                self.is_unknown = bool(err and err.startswith("Unknown call"))
                return self._send(200, {"error": err} if err else {"result": result})
            if self.path == "/sim/inject":
                try:
                    cluster.inject(req)
                except (KeyError, ValueError) as e:
                    return self._send(400, {"error": str(e)})
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

        def log_message(self, *args):
            pass

    return Handler


def main() -> None:
    inventory = Path(sys.argv[1] if len(sys.argv) > 1 else "/inventory.json")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8081
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(Cluster(load_nodes(inventory)))).serve_forever()


if __name__ == "__main__":
    main()
