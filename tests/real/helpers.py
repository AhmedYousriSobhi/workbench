"""Shared helpers for real-host tests: wait loops, a live governor process, its socket client."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GB = 1024**3


def wait_for(pred, timeout: float = 15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


def signals(path: Path, avail_gb: float = 8, some: float = 0, full: float = 0) -> None:
    path.write_text(json.dumps({"mem_available": int(avail_gb * GB), "psi_some": some, "psi_full": full}))


def ask(sock: Path, req: dict) -> dict:
    with socket.socket(socket.AF_UNIX) as c:
        c.connect(str(sock))
        c.sendall(json.dumps(req).encode() + b"\n")
        return json.loads(c.makefile().readline())


def start_governor(tmp: Path, *extra: str, env: dict | None = None, avail_gb: float = 8):
    """Launch a real governor driven by a signals file; returns (process, signals_file, socket)."""
    sig, sock = tmp / "sig.json", tmp / "gov.sock"
    signals(sig, avail_gb)
    proc = subprocess.Popen(
        [sys.executable, "-m", "governor", "--signals-file", str(sig), "--socket", str(sock),
         "--interval", "0.2", *extra], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env={**os.environ, **(env or {})})
    assert wait_for(sock.exists), "governor did not create its socket"
    return proc, sig, sock


def stop_governor(proc) -> bytes:
    proc.terminate()
    _, err = proc.communicate(timeout=10)
    return err
