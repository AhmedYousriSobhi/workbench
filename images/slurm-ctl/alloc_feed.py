"""GPU allocations of running Slurm jobs, for the GPU exporter (internal network only).

GET /alloc  ->  {"nodes": {"gpu001": [0, 1]}}   GPU indexes that running jobs hold, per node
"""
import json
import re
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9401
TTL = 2.0  # seconds a scontrol answer is reused, so a scrape burst costs one call

BLOCK = re.compile(r"Nodes=(\S+) CPU_IDs=\S+ Mem=\S+ GRES=(\S*)")
IDX = re.compile(r"IDX:([0-9,\-]+)")


def expand_nodes(spec: str) -> list[str]:
    """gpu[001-002,004] -> gpu001, gpu002, gpu004 (a plain name stays as it is)."""
    out: list[str] = []
    for m in re.finditer(r"([^,\[]+)(?:\[([^\]]+)\])?", spec):
        prefix, ranges = m.group(1), m.group(2)
        if not ranges:
            out.append(prefix)
            continue
        for part in ranges.split(","):
            lo, _, hi = part.partition("-")
            width = len(lo)
            out += [f"{prefix}{i:0{width}d}" for i in range(int(lo), int(hi or lo) + 1)]
    return out


def indexes(text: str) -> list[int]:
    """0-1,3 -> [0, 1, 3]"""
    out: list[int] = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += range(int(lo), int(hi or lo) + 1)
    return out


def parse_jobs(text: str) -> dict[str, list[int]]:
    """`scontrol show job -d -o` output -> {node: [gpu index]} for RUNNING jobs."""
    held: dict[str, set[int]] = {}
    for line in text.splitlines():
        if "JobState=RUNNING" not in line:
            continue
        for nodes, gres in BLOCK.findall(line):
            m = IDX.search(gres) if gres.startswith("gpu") else None
            if not m:
                continue
            for n in expand_nodes(nodes):
                held.setdefault(n, set()).update(indexes(m.group(1)))
    return {n: sorted(g) for n, g in held.items()}


_cache: tuple[float, dict] = (0.0, {})


def current() -> dict[str, list[int]]:
    global _cache
    if time.monotonic() - _cache[0] > TTL:
        try:
            r = subprocess.run(["scontrol", "show", "job", "-d", "-o"], capture_output=True, text=True, timeout=5, check=False)
            _cache = (time.monotonic(), parse_jobs(r.stdout) if r.returncode == 0 else {})
        except (OSError, subprocess.TimeoutExpired):
            _cache = (time.monotonic(), {})
    return _cache[1]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/alloc"
        body = json.dumps({"nodes": current()} if ok else {"error": "not found"}).encode()
        self.send_response(200 if ok else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
