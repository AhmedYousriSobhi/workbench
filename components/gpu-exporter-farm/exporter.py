"""Fake DCGM-style exporter: one process serving metrics for every simulated node.

GET  /metrics      Prometheus text format
POST /sim/inject   {"node": "gpu001", "xid": 79} | {"node": ..., "temp": 92} | {"node": ..., "clear": true}
"""
import json
import os
import random
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

MOUNT = "/mnt/lustre"
# Framebuffer size per sim.yaml GPU type (MiB); DCGM reports used and free, clients add them for the total.
FB_TOTAL_MIB = {"h200": 143771, "h100": 81559, "a100": 81920, "l40s": 46068}
FB_DEFAULT_MIB = 81920
MODEL_NAME = {"h200": "NVIDIA H200", "h100": "NVIDIA H100 80GB HBM3", "a100": "NVIDIA A100-SXM4-80GB",
              "l40s": "NVIDIA L40S"}
FS_SIZE = 500 * 1024**4
ALLOC_URL = os.environ.get("ALLOC_URL", "http://slurmctld:9401/alloc")  # which GPUs running Slurm jobs hold
ALLOC_STALE_S = 30  # keep the last answer this long when the feed is unreachable, then show every GPU idle


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


def load_gpu_type(sim_yaml: Path) -> str:
    return str(yaml.safe_load(sim_yaml.read_text()).get("gpu", {}).get("type", "")).lower()


class State:
    def __init__(self, nodes: dict[str, int], rng: random.Random | None = None, gpu_type: str = ""):
        self.nodes = nodes
        self.fb_total = FB_TOTAL_MIB.get(gpu_type, FB_DEFAULT_MIB)
        self.model = MODEL_NAME.get(gpu_type, gpu_type.upper() or "NVIDIA GPU")
        self.rng = rng or random.Random()
        self.xid: dict[str, int] = {}
        self.temp_override: dict[str, float] = {}
        self.xid_count: dict[str, int] = {}
        self.down: set[str] = set()  # powered-off nodes publish nothing, as a real exporter on a dead host
        self.alloc: dict[str, set[int]] = {}  # node -> GPU indexes held by running jobs
        self.ib_down: dict[str, int] = {}     # node -> how many InfiniBand ports are down (the first N)
        self.flapping: set[str] = set()       # nodes whose NIC link keeps changing: the carrier counter rises on every scrape
        self.carrier: dict[str, int] = {}

    def set_alloc(self, nodes: dict[str, list[int]]) -> None:
        self.alloc = {n: set(g) for n, g in nodes.items()}

    def ib_ports(self, node: str) -> int:
        """One HCA per GPU on a GPU node, one port on a compute node."""
        return max(1, self.nodes[node])

    def inject(self, req: dict) -> None:
        node = req["node"]
        if node not in self.nodes:
            raise KeyError(node)
        if "down" in req:
            (self.down.add if req["down"] else self.down.discard)(node)
        if req.get("clear"):
            for gone in (self.xid, self.temp_override, self.ib_down):
                gone.pop(node, None)
            self.flapping.discard(node)
            return
        if "ib_down" in req:
            n = int(req["ib_down"])
            if not 0 <= n <= self.ib_ports(node):
                raise ValueError(f"ib_down must be 0 to {self.ib_ports(node)}")
            if n:
                self.ib_down[node] = n
            else:
                self.ib_down.pop(node, None)
        if "nic_flap" in req:
            (self.flapping.add if req["nic_flap"] else self.flapping.discard)(node)
        if "xid" in req:
            self.xid[node] = int(req["xid"])
            self.xid_count[node] = self.xid_count.get(node, 0) + 1
        if "temp" in req:
            self.temp_override[node] = float(req["temp"])

    def render(self) -> str:
        r = self.rng
        out: list[str] = []
        nodes = {n: g for n, g in self.nodes.items() if n not in self.down}

        def head(name: str, help_: str, typ: str = "gauge") -> None:
            out.append(f"# HELP {name} {help_}")
            out.append(f"# TYPE {name} {typ}")

        def busy(n: str, g: int) -> bool:
            return g in self.alloc.get(n, ())

        # A GPU a job holds works hard; every other GPU idles, so usage follows the scheduler.
        used = {(n, g): r.randint(int(self.fb_total * 0.6), int(self.fb_total * 0.95)) if busy(n, g) else r.randint(300, 700)
                for n, gpus in nodes.items() for g in range(gpus)}

        def per_gpu(name: str, help_: str, value) -> None:
            """`node` is this sim's own label; the rest are the labels the real DCGM exporter puts on every series
            (clients filter on Hostname and read modelName)."""
            head(name, help_)
            for n, gpus in nodes.items():
                for g in range(gpus):
                    labels = (f'node="{n}",gpu="{g}",Hostname="{n}",device="nvidia{g}",'
                              f'UUID="GPU-{n}-{g:02d}",modelName="{self.model}"')
                    out.append(f"{name}{{{labels}}} {value(n, g)}")

        def per_node(name: str, help_: str, value, labels: str = "", typ: str = "gauge") -> None:
            head(name, help_, typ)
            for n in nodes:
                out.append(f'{name}{{node="{n}"{labels}}} {value(n)}')

        per_gpu("DCGM_FI_DEV_GPU_UTIL", "GPU utilization (percent)", lambda n, g: r.randint(70, 100) if busy(n, g) else r.randint(0, 3))
        per_gpu("DCGM_FI_DEV_FB_USED", "Framebuffer used (MiB)", lambda n, g: used[n, g])
        per_gpu("DCGM_FI_DEV_FB_FREE", "Framebuffer free (MiB)", lambda n, g: self.fb_total - used[n, g])
        per_gpu(
            "DCGM_FI_DEV_GPU_TEMP",
            "GPU temperature (C)",
            lambda n, g: f"{self.temp_override.get(n, r.uniform(60, 80) if busy(n, g) else r.uniform(35, 45)):.1f}",
        )
        per_gpu("DCGM_FI_DEV_POWER_USAGE", "Power draw (W)", lambda n, g: f"{r.uniform(400, 650) if busy(n, g) else r.uniform(70, 95):.1f}")
        per_gpu("DCGM_FI_DEV_XID_ERRORS", "Last XID error code (0 = none)", lambda n, g: self.xid.get(n, 0))
        per_node(
            "sim_gpu_xid_events_total",
            "Injected XID events",
            lambda n: self.xid_count.get(n, 0),
            typ="counter",
        )
        per_node("hpc_jobs_running", "Synthetic running jobs", lambda n: r.randint(1, 6))
        per_node("hpc_jobs_pending", "Synthetic pending jobs", lambda n: r.randint(0, 3))
        head("node_infiniband_state_id", "InfiniBand port state (4 = active, 1 = down)")
        for n in nodes:
            for i in range(self.ib_ports(n)):
                state = 1 if i < self.ib_down.get(n, 0) else 4
                out.append(f'node_infiniband_state_id{{node="{n}",Hostname="{n}",device="mlx5_{i}",port="1"}} {state}')
        for n in self.flapping:
            self.carrier[n] = self.carrier.get(n, 0) + 2
        per_node("node_network_carrier_changes_total", "Link state changes", lambda n: self.carrier.get(n, 0), ',device="eth0"', "counter")
        per_node("sim_nic_flapping", "1 while a NIC flap is injected", lambda n: int(n in self.flapping))
        mp = f',mountpoint="{MOUNT}"'
        per_node("node_filesystem_size_bytes", "Fake filesystem size", lambda n: FS_SIZE, mp)
        per_node(
            "node_filesystem_free_bytes",
            "Fake filesystem free",
            lambda n: int(FS_SIZE * r.uniform(0.2, 0.6)),
            mp,
        )
        return "\n".join(out) + "\n"


PROBE = "x-workbench-probe"  # the web UI sets this on its own polls so they are not drawn as client traffic


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


def fetch_alloc(url: str = ALLOC_URL, timeout: float = 2) -> dict[str, list[int]]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)["nodes"]


def follow_slurm(state: State, fetch=fetch_alloc, interval: float = 3, stop: threading.Event | None = None) -> None:
    """Refresh the allocation until stopped; an unreachable feed keeps the last answer for ALLOC_STALE_S, then idles."""
    stop = stop or threading.Event()
    last_ok = time.monotonic()
    while not stop.is_set():
        try:
            state.set_alloc(fetch())
            last_ok = time.monotonic()
        except (OSError, ValueError, KeyError):
            if time.monotonic() - last_ok > ALLOC_STALE_S:
                state.set_alloc({})
        stop.wait(interval)


def main() -> None:
    sim = Path(sys.argv[1] if len(sys.argv) > 1 else "sim.yaml")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9400
    state = State(load_nodes(sim), gpu_type=load_gpu_type(sim))
    threading.Thread(target=follow_slurm, args=(state,), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(state)).serve_forever()


if __name__ == "__main__":
    main()
