"""Bring the simulated cluster up and down in dependency order (SPEC G4, section 7).

Docker is driven through `docker compose` against the generated file; every call goes through the
injectable `runner` so the ordering logic is testable without Docker.
"""
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from . import checks, compose

COMPOSE_FILE = Path("compose/generated/docker-compose.yml")
PROJECT = "hpcsim"
STAGES = [  # (label, service-name pattern); later stages depend on earlier ones
    ("observability", r"prometheus|grafana|gpu-exporter-farm"),
    ("slurm core", r"slurmctld|mariadb|slurmdbd"),
    ("slurm services", r"slurmrestd|accounting-init"),
    ("mocks", r"bmc-farm|bcm-mock|jira-mock"),
    ("nodes", r"(cn|gpu)\d+"),
    ("scenarios", r"scenario-engine"),
]

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=600)


def dc(*args: str, file: Path = COMPOSE_FILE) -> list[str]:
    return ["docker", "compose", "-p", PROJECT, "-f", str(file), *args]


def stage_services(services: list[str]) -> list[tuple[str, list[str]]]:
    """Group service names into ordered start stages; anything unmatched goes last."""
    left, out = list(services), []
    for label, pat in STAGES:
        hit = [s for s in left if re.fullmatch(pat, s)]
        if hit:
            out.append((label, hit))
            left = [s for s in left if s not in hit]
    if left:
        out.append(("other", left))
    return out


def unbuildable(services: dict, base: Path) -> list[str]:
    """Services whose build context has no Dockerfile: components not implemented yet."""
    return [n for n, s in services.items()
            if s.get("build") and not (base / s["build"] / "Dockerfile").is_file()]


def admit(profile: str, available: int | None = None) -> tuple[bool, str]:
    """Pre-flight: refuse unless the profile fits, naming a smaller profile that does."""
    avail = checks.mem_available() if available is None else available
    return checks.preflight(profile, checks.load_profiles(), avail)


def wait_running(names: list[str], runner: Runner = _run, timeout: float = 120,
                 sleep: Callable[[float], None] = time.sleep) -> list[str]:
    """Return the services still not running after `timeout` seconds (empty when all are up)."""
    pending, end = list(names), time.monotonic() + timeout
    while pending:
        r = runner(dc("ps", "--status", "running", "--format", "{{.Service}}"))
        up = set(r.stdout.split())
        pending = [n for n in pending if n not in up]
        if not pending or time.monotonic() >= end:
            break
        sleep(2)
    return pending


def up(sim: dict, profile: str, runner: Runner = _run, say: Callable[[str], None] = print,
       available: int | None = None, wait: float = 120) -> bool:
    ok, msg = admit(profile, available)
    say(("admitted: " if ok else "REFUSED: ") + msg)
    if not ok:
        return False
    prof = compose.load_profile(profile)
    data = compose.generate(sim, prof)
    path = compose.write(data, sim=sim, compute_nodes=prof.get("compute_nodes"))
    skip = unbuildable(data["services"], path.parent)
    if skip:
        say(f"skipping (not implemented yet): {', '.join(skip)}")
    names = [n for n in data["services"] if n not in skip] + ["prometheus", "grafana", "gpu-exporter-farm"]
    for label, svcs in stage_services(names):
        say(f"starting {label}: {', '.join(svcs)}")
        r = runner(dc("up", "-d", "--no-deps", *svcs, file=path))
        if r.returncode != 0:
            say(f"FAILED {label}: {r.stderr.strip()[-300:]}")
            return False
        if (late := wait_running(svcs, runner, wait)):
            say(f"FAILED {label}: not running: {', '.join(late)}")
            return False
    try:
        say(f"grafana token: {provision_token()}")
    except OSError as e:
        say(f"WARN no grafana token ({e}); the governor will not publish annotations")
    say("stack is up")
    return True


def down(runner: Runner = _run, say: Callable[[str], None] = print) -> bool:
    """Stop and remove only this compose project; the user's own containers/VMs are untouched."""
    r = runner(dc("down", "--remove-orphans"))
    say("stack is down" if r.returncode == 0 else f"FAILED: {r.stderr.strip()[-300:]}")
    return r.returncode == 0


def running_nodes(runner: Runner = _run) -> list[str]:
    r = runner(dc("ps", "--format", "{{.Service}}"))
    return sorted(s for s in r.stdout.split() if re.fullmatch(r"cn\d+", s))


def scale(sim: dict, profile: str, n: int, runner: Runner = _run,
          say: Callable[[str], None] = print) -> bool:
    """Set the compute-node count: regenerate slurm.conf, restart slurmctld, start/stop slurmd."""
    prof = compose.load_profile(profile)
    data = compose.generate(sim, {**prof, "compute_nodes": n})
    path = compose.write(data, sim=sim, compute_nodes=n)
    want = [s for s in data["services"] if re.fullmatch(r"cn\d+", s)]
    stop = [s for s in running_nodes(runner) if s not in want]
    if stop:
        say(f"stopping {', '.join(stop)}")
        runner(dc("rm", "-sf", *stop, file=path))
    r = runner(dc("up", "-d", "--no-deps", "--force-recreate", "slurmctld", file=path))
    if r.returncode == 0 and want:
        say(f"starting {', '.join(want)}")
        r = runner(dc("up", "-d", "--no-deps", *want, file=path))
    ok = r.returncode == 0 and not wait_running(["slurmctld", *want], runner)
    say(f"compute nodes: {n}" if ok else f"FAILED: {r.stderr.strip()[-300:]}")
    return ok


TOKEN_FILE = Path("compose/generated/secrets/grafana_token")


def _grafana(method: str, path: str, body: dict | None, url: str, auth: str) -> dict:
    import base64
    import json
    import urllib.request

    req = urllib.request.Request(
        url + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Basic " + base64.b64encode(auth.encode()).decode()})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def provision_token(url: str = "http://127.0.0.1:3000", auth: str = "admin:admin",
                    path: Path = TOKEN_FILE, http=_grafana) -> Path:
    """Create the governor's annotation service-account token once; the file (0600) is the only copy."""
    if path.exists():
        return path
    sa = http("POST", "/api/serviceaccounts", {"name": "hpcsim-governor", "role": "Editor"}, url, auth)
    tok = http("POST", f"/api/serviceaccounts/{sa['id']}/tokens", {"name": "governor"}, url, auth)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.touch(mode=0o600)
    path.write_text(tok["key"])
    return path
