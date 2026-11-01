"""Fake BCM CMDaemon: the JSON API subset the app reads, one process, nodes from sim.yaml.

POST /json  {"service": "cmdevice", "call": "getDevices"}
            {"service": "cmdevice", "call": "getDevice", "args": ["cn001"]}
            {"service": "cmdevice", "call": "powerOn|powerOff|powerReset", "args": ["cn001"]}
            {"service": "cmstatus", "call": "getStatus", "args": ["cn001"]}
            {"service": "cmpart", "call": "getCategories"}
GET  /healthz
POST /sim/inject  {"node": n, "state": "UP|DOWN|CLOSED|INSTALLING"} | {"node": n, "closed": true}

The call names follow CMDaemon's service/call convention but are an assumed subset (ADR 0005); extend CALLS.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

STATES = ("UP", "DOWN", "CLOSED", "INSTALLING")
POWER_TO_STATE = {"powerOn": "UP", "powerOff": "DOWN", "powerReset": "UP"}


def load_nodes(sim_yaml: Path) -> dict[str, dict]:
    """{node: {"category": c, "gpus": n, "memory_mb": m, "cpus": c}} for compute then GPU nodes."""
    cfg = yaml.safe_load(sim_yaml.read_text())
    out: dict[str, dict] = {}
    c, g = cfg.get("compute", {}), cfg.get("gpu", {})
    for i in range(1, c.get("count", 0) + 1):
        out[f"{c['prefix']}{i:03d}"] = {"category": "compute", "gpus": 0, "memory_mb": c.get("memory_mb", 4096),
                                        "cpus": c.get("cpus", 8)}
    for i in range(1, g.get("nodes", 0) + 1):
        out[f"{g['prefix']}{i:03d}"] = {"category": "gpu", "gpus": g["gpus_per_node"],
                                        "memory_mb": 2048 * g["gpus_per_node"], "cpus": 8 * g["gpus_per_node"]}
    return out


class Cluster:
    def __init__(self, nodes: dict[str, dict]):
        self.nodes = nodes
        self.state = {n: "UP" for n in nodes}

    def device(self, name: str) -> dict:
        n = self.nodes[name]
        return {"hostname": name, "childType": "PhysicalNode", "category": n["category"], "ip": self.ip(name),
                "gpus": n["gpus"], "memory_mb": n["memory_mb"], "cpus": n["cpus"], "status": self.state[name]}

    def ip(self, name: str) -> str:
        kind = self.nodes[name]["category"]  # the same formula as benchctl/addressing.py host_ip (this file cannot import it)
        i = [n for n, v in self.nodes.items() if v["category"] == kind].index(name)
        return f"10.141.{i // 254 + (100 if kind == 'gpu' else 0)}.{i % 254 + 1}"

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


def make_handler(cluster: Cluster):
    stats, lock = {"requests": 0}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj) -> None:
            if counted(self.path, self.headers):
                with lock:
                    stats["requests"] += 1
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
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            if self.path == "/json":
                result, err = cluster.call(req)
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
    sim = Path(sys.argv[1] if len(sys.argv) > 1 else "sim.yaml")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8081
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(Cluster(load_nodes(sim)))).serve_forever()


if __name__ == "__main__":
    main()
