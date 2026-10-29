"""Shared helpers for real-host tests: wait loops, a live governor process, its socket client."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from simctl import stack

ROOT = Path(__file__).resolve().parents[2]
GB = 1024**3


def wait_for(pred, timeout: float = 15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


def bring_up(sim: dict, profile: str = "min") -> None:
    """Start the stack for a fixture. On failure say why: a refusal by the governor (host memory low, GV-9) is the
    environment, not a defect in the code under test, so the message says so instead of a bare AssertionError."""
    said: list[str] = []
    if stack.up(sim, profile, say=said.append, available=64 * GB):
        return
    why = " | ".join(said[-4:]) or "no output"
    if "REFUSED by governor" in why:
        raise AssertionError(f"ENVIRONMENT, not a code defect: the governor refused `up` (free host memory, then rerun): {why}")
    raise AssertionError(f"stack.up failed: {why}")


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


def ssh_login(*cmd: str, user: str = "alice", port: int = 2222, key: Path | None = None, known_hosts: Path | None = None,
              opts: tuple[str, ...] = (), tty: bool = False) -> subprocess.CompletedProcess:
    """ssh to the login node as a client would. Without `known_hosts` the host key is not checked; with one it is pinned."""
    key = key or ROOT / stack.SSH_DIR / "id_ed25519"
    pin = ("-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}") if known_hosts else (
        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null")
    return subprocess.run(["ssh", *(["-tt"] if tty else []), "-i", str(key), "-p", str(port), "-o", "IdentitiesOnly=yes",
                           *pin, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", *opts, f"{user}@127.0.0.1", *cmd],
                          capture_output=True, text=True, timeout=60, check=False)
