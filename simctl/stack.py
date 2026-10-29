"""Bring the simulated cluster up and down in dependency order (SPEC G4, section 7).

Docker is driven through `docker compose` against the generated file; every call goes through the
injectable `runner` so the ordering logic is testable without Docker.
"""
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from . import checks, compose

COMPOSE_FILE = Path("compose/generated/docker-compose.yml")
PROJECT = "hpcsim"
PROFILE_FILE = Path("compose/generated/profile")  # the profile `up` last started; scale's default
STAGES = [  # (label, service-name pattern); later stages depend on earlier ones
    ("observability", r"prometheus|grafana|gpu-exporter-farm"),
    ("slurm core", r"slurmctld|mariadb|slurmdbd"),
    ("slurm services", r"slurmrestd|accounting-init"),
    ("mocks", r"bmc-farm|bcm-mock|jira-mock"),
    ("nodes", r"(cn|gpu)\d+"),
    ("login node", r"login"),
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


def governor_admits(need_mb: int, say: Callable[[str], None], ask=None) -> bool:
    """GV-9: ask the governor to approve starting `need_mb` more. No governor reachable: warn, go on.

    The socket is $HPCSIM_GOV_SOCKET when set (tests), else the standard path.
    """
    sock = os.environ.get("HPCSIM_GOV_SOCKET", checks.GOV_SOCKET)
    ask = ask or (lambda r: checks.governor_request(r, sock))
    try:
        r = ask({"cmd": "admit", "budget_mb": need_mb})
    except OSError:
        say("WARN governor not reachable: starting without admission (GV-9)")
        return True
    if not r.get("ok"):
        say(f"REFUSED by governor: {r.get('error')}")
    return bool(r.get("ok"))


def missing_mb(services: dict, runner: Runner, file: Path) -> int:
    """Memory limits of the services that are not running yet (what a start would add)."""
    up_now = set(runner(dc("ps", "--status", "running", "--format", "{{.Service}}", file=file)).stdout.split())
    return sum(int(s["mem_limit"].rstrip("m")) for n, s in services.items() if n not in up_now)


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
    prof = compose.load_profile(profile)
    data = compose.generate(sim, prof)
    path = compose.write(data, sim=sim, compute_nodes=prof.get("compute_nodes"))
    skip = unbuildable(data["services"], path.parent)
    todo = {n: v for n, v in data["services"].items() if n not in skip}
    missing = missing_mb(todo, runner, path)
    if missing:  # a running stack is already in "used" memory: nothing to admit when nothing starts
        ok, msg = admit(profile, available)
        say(("admitted: " if ok else "REFUSED: ") + msg)
        if not ok or not governor_admits(missing, say):
            return False
    else:
        say(f"admitted: {profile}: every service is already running")
    PROFILE_FILE.write_text(profile)
    if skip:
        say(f"skipping (not implemented yet): {', '.join(skip)}")
    if "bmc-farm" in todo:
        ensure_bmc_credentials()
    if "login" in todo:
        try:
            ensure_ssh_keys()
        except OSError as e:
            say(f"FAILED login node keys: {e}")
            return False
    names = [n for n in data["services"] if n not in skip] + ["prometheus", "grafana", "gpu-exporter-farm"]
    for label, svcs in stage_services(names):
        say(f"starting {label}: {', '.join(svcs)}")
        r = runner(dc("up", "-d", "--build", "--no-deps", *svcs, file=path))
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
    if "bmc-farm" in todo:
        say(f"bmc: https://127.0.0.1:8443/redfish/v1 (Host bmc-<node>), user admin, password in {BMC_CREDENTIALS}")
    if "login" in todo:
        say(f"ssh: ssh -i {SSH_DIR}/id_ed25519 -p {compose.SSH_PORT} <user>@127.0.0.1 (users from sim.yaml)")
    say("stack is up")
    return True


def down(runner: Runner = _run, say: Callable[[str], None] = print) -> bool:
    """Stop and remove only this compose project; the user's own containers/VMs are untouched."""
    r = runner(dc("down", "--remove-orphans"))
    say("stack is down" if r.returncode == 0 else f"FAILED: {r.stderr.strip()[-300:]}")
    return r.returncode == 0


def _project_services(runner: Runner, status: str = "running") -> list[str]:
    """Service names of this project's containers, straight from Docker (not from the compose file)."""
    r = runner(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}",
                "--filter", f"status={status}", "--format", '{{.Label "com.docker.compose.service"}}'])
    return r.stdout.split()


def running_nodes(runner: Runner = _run) -> list[str]:
    return sorted(s for s in _project_services(runner) if re.fullmatch(r"cn\d+", s))


def remove_services(names: list[str], runner: Runner = _run) -> None:
    """Remove containers by label. `compose rm` cannot: the regenerated file no longer lists them."""
    for n in names:
        r = runner(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}",
                    "--filter", f"label=com.docker.compose.service={n}"])
        if r.stdout.split():
            runner(["docker", "rm", "-f", *r.stdout.split()])


def current_profile(default: str = "std") -> str:
    return PROFILE_FILE.read_text().strip() if PROFILE_FILE.is_file() else default


def scale(sim: dict, profile: str, n: int, runner: Runner = _run,
          say: Callable[[str], None] = print) -> bool:
    """Set the compute-node count: regenerate slurm.conf, restart slurmctld, start/stop slurmd."""
    prof = compose.load_profile(profile)
    data = compose.generate(sim, {**prof, "compute_nodes": n})
    path = compose.write(data, sim=sim, compute_nodes=n)
    want = [s for s in data["services"] if re.fullmatch(r"cn\d+", s)]
    now = running_nodes(runner)
    grow = [s for s in want if s not in now]
    if grow and not governor_admits(sum(int(data["services"][g]["mem_limit"].rstrip("m")) for g in grow), say):
        return False
    stop = [s for s in now if s not in want]
    if stop:
        say(f"stopping {', '.join(stop)}")
        remove_services(stop, runner)
    r = runner(dc("up", "-d", "--no-deps", "--force-recreate", "slurmctld", file=path))
    if r.returncode == 0 and want:
        say(f"starting {', '.join(want)}")
        r = runner(dc("up", "-d", "--no-deps", *want, file=path))
    ok = r.returncode == 0 and not wait_running(["slurmctld", *want], runner)
    say(f"compute nodes: {n}" if ok else f"FAILED: {r.stderr.strip()[-300:]}")
    return ok


SSH_DIR = Path("compose/generated/secrets/ssh")


def ensure_ssh_keys(path: Path = SSH_DIR, runner: Runner = _run) -> Path:
    """Host key, one client key and authorized_keys for the login node (0600, never tracked).

    Kept across `up`s so a client that pinned the host key keeps trusting it.
    """
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("ssh_host_ed25519_key", "id_ed25519"):
        if not (path / name).exists():
            r = runner(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"hpcsim-{name}", "-f", str(path / name)])
            if r.returncode != 0:
                raise OSError(f"ssh-keygen failed: {r.stderr.strip()[-200:]}")
    (path / "authorized_keys").write_text((path / "id_ed25519.pub").read_text())
    if os.geteuid() == 0:  # run under sudo: hand the secrets back to the checkout's owner
        owner = path.parent.parent.stat()
        for p in (path, *path.iterdir()):
            os.chown(p, owner.st_uid, owner.st_gid)
    return path


BMC_CREDENTIALS = Path("compose/generated/secrets/bmc_credentials")


def ensure_bmc_credentials(path: Path = BMC_CREDENTIALS) -> Path:
    """'admin:<random>' for the bmc-farm's TLS surface, made once. Mounted read-only into a container that runs as
    `nobody`, so the file is world-readable; the 0700 folder above it keeps other host users out."""
    if path.exists():
        return path
    import secrets

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(f"admin:{secrets.token_urlsafe(18)}\n")
    path.chmod(0o644)
    if os.geteuid() == 0:
        owner = path.parent.parent.stat()
        for p in (path.parent, path):
            os.chown(p, owner.st_uid, owner.st_gid)
    return path


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


def token_valid(token: str, url: str = "http://127.0.0.1:3000") -> bool:
    """Does Grafana accept this service-account token? (False also when Grafana is unreachable.)"""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url + "/api/org", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5):
            return True
    except (urllib.error.URLError, OSError):
        return False


def provision_token(url: str = "http://127.0.0.1:3000", auth: str = "admin:admin",
                    path: Path = TOKEN_FILE, http=_grafana, valid=token_valid,
                    retries: int = 30, sleep: Callable[[float], None] = time.sleep) -> Path:
    """Ensure the governor's annotation token exists and works; the 0600 file is the only copy.

    Grafana's state is disposable (no volume), so a token left over from a previous `up` is
    replaced. Grafana needs a moment after start, hence the retries.
    """
    if path.exists() and valid(path.read_text().strip(), url):
        return path
    for attempt in range(retries):
        try:
            sa = http("POST", "/api/serviceaccounts", {"name": "hpcsim-governor", "role": "Editor"}, url, auth)
            break
        except OSError:
            if attempt == retries - 1:
                raise
            sleep(2)
    tok = http("POST", f"/api/serviceaccounts/{sa['id']}/tokens", {"name": "governor"}, url, auth)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.unlink(missing_ok=True)
    path.touch(mode=0o600)
    path.write_text(tok["key"])
    if os.geteuid() == 0:  # run under sudo: hand the secret back to the checkout's owner
        owner = path.parent.parent.stat()
        for p in (path.parent, path):
            os.chown(p, owner.st_uid, owner.st_gid)
    return path
