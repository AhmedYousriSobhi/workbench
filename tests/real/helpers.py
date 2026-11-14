"""Shared helpers for real-host tests: wait loops, a live governor process, its socket client."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import stack_reuse

from benchctl import stack

ROOT = Path(__file__).resolve().parents[2]
GB = 1024**3
STACK_KEY: str | None = None  # key of the running stack (stack_reuse.stack_key); a different key is a different stack (issue 576)
STACK_STARTED = False  # set by bring_up; conftest brings the shared stack down once, at the end of the session (issue 234)


def wait_for(pred, timeout: float = 15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


def exporter_up() -> bool:
    """True once the GPU exporter serves metrics. pyyaml is baked into its image (issue 235), so this is a few seconds after
    `up`; until then the port answers with a reset or nothing, so tests that read it still wait."""
    import urllib.request
    try:
        return b"DCGM_FI_DEV_GPU_UTIL" in urllib.request.urlopen("http://127.0.0.1:9400/metrics", timeout=3).read()
    except OSError:
        return False


def bring_up(sim: dict, profile: str = "min", prof: dict | None = None) -> None:
    """Start the stack for a fixture. On failure say why: a refusal by the governor (host memory low, GV-9) is the
    environment, not a defect in the code under test, so the message says so instead of a bare AssertionError."""
    global STACK_STARTED, STACK_KEY
    said: list[str] = []
    key = stack_reuse.stack_key(sim, profile, prof)
    if STACK_STARTED and STACK_KEY != key:  # a different configuration never meets a running stack (issue 535); the same one is reused
        os.chdir(ROOT)
        stack.down(say=lambda m: None)
        STACK_STARTED = False
    if stack.up(sim, profile, say=said.append, available=64 * GB, **({"prof": prof} if prof else {})):
        STACK_STARTED, STACK_KEY = True, key
        return
    why = " | ".join(said[-4:]) or "no output"
    if "REFUSED by governor" in why:
        raise AssertionError(f"ENVIRONMENT, not a code defect: the governor refused `up` (free host memory, then rerun): {why}")
    raise AssertionError(f"stack.up failed: {why}\n{diagnostics()}")


def diagnostics() -> str:
    """Why a stack may not start, for a failure message (issue 528): containers left paused, and the governor's level with its
    last audit lines (a freeze names who paused what). Best effort: a missing Docker or governor is itself said."""
    out = []
    try:
        r = subprocess.run(["docker", "ps", "-a", "--filter", "status=paused", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=20, check=False)
        out.append("paused containers: " + (", ".join(r.stdout.split()) or "none"))
    except (OSError, subprocess.SubprocessError) as e:
        out.append(f"paused containers: not read ({e})")
    try:
        from benchctl import checks
        st = checks.governor_request({"cmd": "status"}, os.environ.get("WORKBENCH_GOV_SOCKET", "/run/workbench/gov.sock"))
        out.append(f"governor: level L{st.get('level')}, mem_available {int(st.get('mem_available') or 0) // 2**20} MB; last audit: " + " | ".join(st.get("audit", [])[-6:]))
    except (OSError, ValueError, KeyError) as e:
        out.append(f"governor: not read ({e})")
    return "\n".join(out)


def release() -> None:
    """End of a module that uses the shared stack (issue 234): leave it running for the next module. `up` is idempotent, so
    the next fixture finds every service running and only waits for its own readiness; conftest stops it once at the end,
    or before a module in ISOLATED_MODULES that needs a cold start of its own."""


def signals(path: Path, avail_gb: float = 8, some: float = 0, full: float = 0, swap: float = 0) -> None:
    path.write_text(json.dumps({"mem_available": int(avail_gb * GB), "psi_some": some, "psi_full": full, "swap_out_rate": swap}))


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


def prom_up(job: str, prom: str = "http://127.0.0.1:9090") -> dict[str, str]:
    """`{instance: "1" | "0"}` for one scrape job, as Prometheus itself reports it ({} while it has no such target yet)."""
    import json
    import urllib.parse
    import urllib.request
    try:
        q = urllib.parse.quote(f'up{{job="{job}"}}')
        with urllib.request.urlopen(f"{prom}/api/v1/query?query={q}", timeout=5) as r:
            return {x["metric"].get("instance", "?"): x["value"][1] for x in json.loads(r.read())["data"]["result"]}
    except (OSError, ValueError, KeyError):
        return {}
