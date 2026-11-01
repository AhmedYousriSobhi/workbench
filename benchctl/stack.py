"""Bring the simulated cluster up and down in dependency order (SPEC G4, section 7).

Docker is driven through `docker compose` against the generated file; every call goes through the
injectable `runner` so the ordering logic is testable without Docker.
"""
import os
import re
import subprocess
import time
import urllib.error
from collections.abc import Callable
from pathlib import Path

from . import addressing, checks, compose, logins, teleport, workbench

COMPOSE_FILE = Path("compose/generated/docker-compose.yml")
PROJECT = "workbench"
PROFILE_FILE = Path("compose/generated/profile")  # the profile `up` last started; scale's default
STAGES = [  # (label, service-name pattern); later stages depend on earlier ones
    ("observability", r"prometheus|grafana|gpu-exporter-farm"),
    ("slurm core", r"slurmctld|mariadb|slurmdbd"),
    ("slurm services", r"slurmrestd|accounting-init"),
    ("mocks", r"bmc-farm|bcm-mock|jira-mock"),
    ("teleport", r"teleport"),
    ("bmc addresses", r"bmc-(cn|gpu)\d+"),
    ("nodes", r"(cn|gpu)\d+"),
    ("login node", r"login\d*"),
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

    The socket is $WORKBENCH_GOV_SOCKET when set (tests), else the standard path.
    """
    sock = os.environ.get("WORKBENCH_GOV_SOCKET", checks.GOV_SOCKET)
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


ONESHOT = {"accounting-init"}  # run once and exit; success is exit code 0, not "running"


def wait_running(names: list[str], runner: Runner = _run, timeout: float = 120,
                 sleep: Callable[[float], None] = time.sleep) -> list[str]:
    """Return the services still not running after `timeout` seconds (empty when all are up)."""
    pending, end = list(names), time.monotonic() + timeout
    while pending:
        r = runner(dc("ps", "--status", "running", "--format", "{{.Service}}"))
        up = set(r.stdout.split())
        if any(n not in up and n in ONESHOT for n in pending):  # a job that ran and exited 0 has started
            done = runner(dc("ps", "-a", "--status", "exited", "--format", "{{.Service}} {{.ExitCode}}")).stdout.splitlines()
            up |= {line.split()[0] for line in done if len(line.split()) == 2 and line.split()[1] == "0"} & ONESHOT
        pending = [n for n in pending if n not in up]
        if not pending or time.monotonic() >= end:
            break
        sleep(2)
    return pending


def network_problem(runner: Runner = _run) -> str | None:
    """Why the fixed addressing cannot be applied, or None. A network that already exists with another subnet cannot be
    changed under running containers; a new one must not overlap a route of the host (a VPN, an office network)."""
    r = runner(["docker", "network", "inspect", f"{PROJECT}_default", "--format", "{{range .IPAM.Config}}{{.Subnet}} {{.Gateway}} {{end}}"])
    have = r.stdout.split() if r.returncode == 0 else []
    if have:
        if addressing.INTERNAL in have and addressing.GATEWAY in have:
            return None
        return (f"the cluster network is {' '.join(have)} but the addressing plan is {addressing.INTERNAL} with gateway "
                f"{addressing.GATEWAY} (ADR 0018): run ./scripts/benchctl down once, then start again")
    clash = addressing.overlaps(runner(["ip", "route"]).stdout)
    if clash:
        return (f"the host already routes {'; '.join(clash)}. The workbench needs {addressing.INTERNAL} and "
                f"{addressing.IPMI}: disconnect that network or free the range")
    return None


def up(sim: dict, profile: str, runner: Runner = _run, say: Callable[[str], None] = print,
       available: int | None = None, wait: float = 120, prof: dict | None = None) -> bool:
    """Start the profile. `prof` is a profile built from a workbench selection (benchctl apply): admitted by its own
    estimate instead of the named profile's budget."""
    chosen = prof
    prof = prof if prof is not None else compose.load_profile(profile)
    try:
        data = compose.generate(sim, prof)
    except logins.LoginError as e:
        say(f"FAILED login nodes: {e}")
        return False
    conf = COMPOSE_FILE.parent / "slurm" / "slurm.conf"  # a changed file needs a controller restart (it only rereads at start)
    old_conf = conf.read_text() if conf.is_file() else None
    path = compose.write(data, sim=sim, compute_nodes=prof.get("compute_nodes"))
    conf_changed = old_conf is not None and conf.is_file() and conf.read_text() != old_conf
    skip = unbuildable(data["services"], path.parent)
    todo = {n: v for n, v in data["services"].items() if n not in skip}
    if (problem := network_problem(runner)):
        say(f"REFUSED: {problem}")
        return False
    missing = missing_mb(todo, runner, path)
    if missing:  # a running stack is already in "used" memory: nothing to admit when nothing starts
        if chosen is None:
            ok, msg = admit(profile, available)
        else:
            avail = checks.mem_available() if available is None else available
            ok, msg = workbench.verdict(workbench.estimate(sim, chosen), avail // (1024 * 1024))
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
    if any(logins.is_login(n) for n in todo) or any(re.fullmatch(r"(cn|gpu)\d+", n) for n in todo):
        try:
            ensure_ssh_keys()
        except OSError as e:
            say(f"FAILED login node keys: {e}")
            return False
    names = [n for n in data["services"] if n not in skip] + ["prometheus", "grafana", "gpu-exporter-farm"]
    node_re = r"(cn|gpu)\d+"
    changed = False
    before = set(_project_services(runner))
    stale_bmc = sorted(s for s in before if re.fullmatch(r"bmc-(cn|gpu)\d+", s) and s not in names)  # a BMC switched off, or its node gone
    if stale_bmc:
        say(f"removing BMCs no longer wanted: {', '.join(stale_bmc)}")
        remove_services(stale_bmc, runner)
    stale_login = sorted(s for s in before if logins.is_login(s) and s not in names)  # fewer login nodes than before
    if stale_login:
        say(f"removing login nodes no longer wanted: {', '.join(stale_login)}")
        remove_services(stale_login, runner)
    # accounting added under a running controller: slurm.conf gains AccountingStorageType, which only a restart reads
    acct_added = "slurmdbd" in todo and "slurmdbd" not in before and "slurmctld" in before
    if chosen is not None:  # a selection can shrink or grow a running stack: drop nodes it no longer wants (issue 117)
        have = [s for s in _project_services(runner) if re.fullmatch(node_re, s)]
        want = [n for n in names if re.fullmatch(node_re, n)]
        stale = [s for s in have if s not in want]
        changed = bool(have) and (bool(stale) or any(n not in have for n in want))
        if stale:
            say(f"removing nodes no longer wanted: {', '.join(stale)}")
            remove_services(stale, runner)
    # slurmctld, slurmrestd and login build FROM node-base's tag, which only a node service rebuilds. Build it first,
    # or a changed base (new munge key) leaves the controller on the old image and every Slurm call fails to authenticate.
    if (first := next((n for n in names if re.fullmatch(r"(cn|gpu)\d+", n)), None)):
        r = runner(dc("build", first, file=path))
        if r.returncode != 0:
            say(f"FAILED node base image: {r.stderr.strip()[-300:]}")
            return False
    ips = addressing.plan(sim, prof.get("compute_nodes"))
    if "teleport" in todo:
        try:
            teleport.prepare(sim, ips)
        except (OSError, teleport.TeleportError) as e:
            say(f"FAILED teleport setup: {e}")
            return False
    for label, svcs in stage_services(names):
        say(f"starting {label}: {', '.join(svcs)}")
        r = runner(dc("up", "-d", "--build", "--no-deps", *svcs, file=path))
        if r.returncode != 0:
            say(f"FAILED {label}: {r.stderr.strip()[-300:]}")
            return False
        if (late := wait_running(svcs, runner, wait)):
            say(f"FAILED {label}: not running: {', '.join(late)}")
            return False
        if label == "teleport" and not teleport.bootstrap(sim, ips, runner, dc, say):  # before the nodes read their certificates
            return False
    if (changed or acct_added or (conf_changed and "slurmctld" in before)) and "slurmctld" in todo:  # slurm.conf changed under a running controller
        say("restarting slurmctld to pick up the new " + ("accounting setup" if acct_added and not changed else "node list" if changed else "slurm.conf"))
        r = runner(dc("up", "-d", "--no-deps", "--force-recreate", "slurmctld", file=path))
        if r.returncode != 0 or wait_running(["slurmctld"], runner, wait):
            say(f"FAILED slurmctld restart: {r.stderr.strip()[-300:]}")
            return False
    try:
        say(f"grafana token: {provision_token()}")
    except OSError as e:
        say(f"WARN no grafana token ({e}); the governor will not publish annotations")
    else:
        try:  # a read-only token for monitoring clients: dashboards and /api/ds/query, no writes
            say(f"grafana viewer token: {provision_token(path=VIEWER_TOKEN_FILE, account='workbench-viewer', role='Viewer')}"
                " (datasource uid: prometheus)")
        except OSError as e:
            say(f"WARN no grafana viewer token ({e})")
    if "bmc-farm" in todo:
        say(f"bmc: https://127.0.0.1:8443/redfish/v1 (Host bmc-<node>), user admin, password in {BMC_CREDENTIALS}")
    for lname, idx in logins.direct(sim) if "login" in todo else []:
        say(f"ssh: ssh -i {SSH_DIR}/id_ed25519 -p {compose.SSH_PORT + idx} <user>@127.0.0.1 (users from sim.yaml; {lname})")
    for lname in logins.via_teleport(sim) if "login" in todo else []:
        say(f"{lname}: reachable through Teleport only: tsh --proxy=127.0.0.1:{teleport.PROXY_PORT} --insecure -i "
            f"{teleport.SECRETS_DIR}/identity/<user> ssh <user>@{lname}")
    if "teleport" in todo:
        say(f"teleport: https://127.0.0.1:{teleport.PROXY_PORT} (self-signed); identity files in "
            f"{teleport.SECRETS_DIR}/identity; password and one-time code for tsh login: ./scripts/benchctl add invite <user>")
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
    """Login host key, one host key shared by the nodes, a client key, a cluster key (login -> node hops) and
    authorized_keys listing both public keys (0600, never tracked).

    Kept across `up`s so a client that pinned the host key keeps trusting it.
    """
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("ssh_host_ed25519_key", "ssh_host_node_ed25519_key", "id_ed25519", "id_cluster"):
        if not (path / name).exists():
            r = runner(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"workbench-{name}", "-f", str(path / name)])
            if r.returncode != 0:
                raise OSError(f"ssh-keygen failed: {r.stderr.strip()[-200:]}")
    (path / "authorized_keys").write_text((path / "id_ed25519.pub").read_text() + (path / "id_cluster.pub").read_text())
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


def token_name(account: str) -> str:
    """Grafana keeps token names unique across the whole organisation, so a re-made token needs a new name."""
    import secrets

    return f"{account.removeprefix('workbench-')}-{secrets.token_hex(3)}"


VIEWER_TOKEN_FILE = Path("compose/generated/secrets/grafana_viewer_token")


def provision_token(url: str = "http://127.0.0.1:3000", auth: str = "admin:admin",
                    path: Path = TOKEN_FILE, http=_grafana, valid=token_valid,
                    retries: int = 30, sleep: Callable[[float], None] = time.sleep,
                    account: str = "workbench-governor", role: str = "Editor") -> Path:
    """Ensure a service-account token exists and works; the 0600 file is the only copy.

    The governor's (Editor, annotations) and a read-only one for monitoring clients (Viewer, dashboards and queries).

    Grafana's state is disposable (no volume), so a token left over from a previous `up` is
    replaced. Grafana needs a moment after start, hence the retries.
    """
    if path.exists() and valid(path.read_text().strip(), url):
        return path
    for attempt in range(retries):
        try:
            sa = http("POST", "/api/serviceaccounts", {"name": account, "role": role}, url, auth)
            break
        except urllib.error.HTTPError as e:
            if e.code != 400:  # 400 is "already exists": a lost token file, the account is reused
                raise
            found = http("GET", f"/api/serviceaccounts/search?query={account}", None, url, auth)
            sa = next((a for a in found.get("serviceAccounts", []) if a["name"] == account), None)
            if sa is None:
                raise
            break
        except OSError:
            if attempt == retries - 1:
                raise
            sleep(2)
    tok = http("POST", f"/api/serviceaccounts/{sa['id']}/tokens", {"name": token_name(account)}, url, auth)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.unlink(missing_ok=True)
    path.touch(mode=0o600)
    path.write_text(tok["key"])
    if os.geteuid() == 0:  # run under sudo: hand the secret back to the checkout's owner
        owner = path.parent.parent.stat()
        for p in (path.parent, path):
            os.chown(p, owner.st_uid, owner.st_gid)
    return path


CLIENT_KEY = "workbench_ed25519"


def install_client_key(dest: Path | None = None, src: Path = SSH_DIR) -> Path:
    """Copy the login key to ~/.ssh for clients that only read keys there (0600); refuses a foreign file."""
    dest = dest or Path.home() / ".ssh" / CLIENT_KEY
    key = src / "id_ed25519"
    if not key.exists():
        raise FileNotFoundError("no login key yet: run `benchctl up` first")
    if dest.exists() and dest.read_bytes() != key.read_bytes():
        raise FileExistsError(f"{dest.name} exists and is not the workbench key; not overwriting it")
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest.write_bytes(key.read_bytes())
    dest.chmod(0o600)
    return dest


def remove_client_key(dest: Path | None = None, src: Path = SSH_DIR) -> bool:
    """Delete the copy made by install_client_key; leaves any other file alone."""
    dest = dest or Path.home() / ".ssh" / CLIENT_KEY
    key = src / "id_ed25519"
    if not dest.exists() or not key.exists() or dest.read_bytes() != key.read_bytes():
        return False
    dest.unlink()
    return True
