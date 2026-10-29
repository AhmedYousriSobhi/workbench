"""Fake DCGM-style exporter: one process serving metrics for every simulated node.

GET  /metrics      Prometheus text format
POST /sim/inject   {"node": "gpu001", "xid": 79} | {"node": ..., "temp": 92} | {"node": ..., "clear": true}
"""
import json
import random
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

MOUNT = "/mnt/lustre"
FS_SIZE = 500 * 1024**4


def load_nodes(sim_yaml: Path) -> dict[str, int]:
    """Return {node_name: gpu_count}; compute nodes have 0 GPUs."""
    cfg = yaml.safe_load(sim_yaml.read_text())
    nodes: dict[str, int] = {}
    c = cfg.get("compute", {})
    for i in range(1, c.get("count", 0) + 1):
        nodes[f"{c['prefix']}{i:03d}"] = 0
    g = cfg.get("gpu", {})
    for i in range(1, g.get("nodes", 0) + 1):
        nodes[f"{g['prefix']}{i:03d}"] = g["gpus_per_node"]
    return nodes


class State:
    def __init__(self, nodes: dict[str, int], rng: random.Random | None = None):
        self.nodes = nodes
        self.rng = rng or random.Random()
        self.xid: dict[str, int] = {}
        self.temp_override: dict[str, float] = {}
        self.xid_count: dict[str, int] = {}

    def inject(self, req: dict) -> None:
        node = req["node"]
        if node not in self.nodes:
            raise KeyError(node)
        if req.get("clear"):
            self.xid.pop(node, None)
            self.temp_override.pop(node, None)
            return
        if "xid" in req:
            self.xid[node] = int(req["xid"])
            self.xid_count[node] = self.xid_count.get(node, 0) + 1
        if "temp" in req:
            self.temp_override[node] = float(req["temp"])

    def render(self) -> str:
        r = self.rng
        out: list[str] = []

        def head(name: str, help_: str, typ: str = "gauge") -> None:
            out.append(f"# HELP {name} {help_}")
            out.append(f"# TYPE {name} {typ}")

        def per_gpu(name: str, help_: str, value) -> None:
            head(name, help_)
            for n, gpus in self.nodes.items():
                for g in range(gpus):
                    out.append(f'{name}{{node="{n}",gpu="{g}"}} {value(n)}')

        def per_node(name: str, help_: str, value, labels: str = "", typ: str = "gauge") -> None:
            head(name, help_, typ)
            for n in self.nodes:
                out.append(f'{name}{{node="{n}"{labels}}} {value(n)}')

        per_gpu("DCGM_FI_DEV_GPU_UTIL", "GPU utilization (percent)", lambda n: r.randint(0, 100))
        per_gpu("DCGM_FI_DEV_FB_USED", "Framebuffer used (MiB)", lambda n: r.randint(1000, 80000))
        per_gpu(
            "DCGM_FI_DEV_GPU_TEMP",
            "GPU temperature (C)",
            lambda n: f"{self.temp_override.get(n, r.uniform(45, 70)):.1f}",
        )
        per_gpu("DCGM_FI_DEV_POWER_USAGE", "Power draw (W)", lambda n: f"{r.uniform(80, 650):.1f}")
        per_gpu("DCGM_FI_DEV_XID_ERRORS", "Last XID error code (0 = none)", lambda n: self.xid.get(n, 0))
        per_node(
            "sim_gpu_xid_events_total",
            "Injected XID events",
            lambda n: self.xid_count.get(n, 0),
            typ="counter",
        )
        per_node("hpc_jobs_running", "Synthetic running jobs", lambda n: r.randint(1, 6))
        per_node("hpc_jobs_pending", "Synthetic pending jobs", lambda n: r.randint(0, 3))
        mp = f',mountpoint="{MOUNT}"'
        per_node("node_filesystem_size_bytes", "Fake filesystem size", lambda n: FS_SIZE, mp)
        per_node(
            "node_filesystem_free_bytes",
            "Fake filesystem free",
            lambda n: int(FS_SIZE * r.uniform(0.2, 0.6)),
            mp,
        )
        return "\n".join(out) + "\n"


PROBE = "x-opti-probe"  # the web UI sets this on its own polls so they are not drawn as client traffic


def counted(path: str, headers) -> bool:
    """A real client request: not a health check, not /sim control, not a UI probe."""
    return not (path.startswith(("/healthz", "/sim/")) or headers.get(PROBE))


def make_handler(state: State):
    stats, lock = {"requests": 0}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str, ctype: str = "text/plain; version=0.0.4") -> None:
            if counted(self.path, self.headers):
                with lock:
                    stats["requests"] += 1
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/sim/stats":
                self._send(200, json.dumps(stats), "application/json")
            elif self.path == "/metrics":
                self._send(200, state.render())
            else:
                self._send(404, "not found\n")

        def do_POST(self):
            if self.path != "/sim/inject":
                return self._send(404, "not found\n")
            try:
                length = int(self.headers.get("Content-Length", 0))
                state.inject(json.loads(self.rfile.read(length)))
            except (KeyError, ValueError, TypeError) as e:
                return self._send(400, f"bad request: {e}\n")
            self._send(200, '{"ok": true}\n', "application/json")

        def log_message(self, *args):
            pass

    return Handler


def main() -> None:
    sim = Path(sys.argv[1] if len(sys.argv) > 1 else "sim.yaml")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9400
    state = State(load_nodes(sim))
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(state)).serve_forever()


if __name__ == "__main__":
    main()
