"""Bring the simulated cluster up and down in dependency order (SPEC G4, section 7).

Docker is driven through `docker compose` against the generated file; every call goes through the
injectable `runner` so the ordering logic is testable without Docker.
"""
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
from collections.abc import Callable
from pathlib import Path

from . import (
    addressing,
    checks,
    compose,
    controller,
    dcgm,
    errors,
    jira_real,
    k8s,
    logins,
    memory,
    promtargets,
    qos,
    ready,
    real_gpu,
    slurmconf,
    teleport,
    topology,
    workbench,
)

COMPOSE_FILE = Path("compose/generated/docker-compose.yml")
PROJECT = "workbench"
PROFILE_FILE = Path("compose/generated/profile")  # the profile `up` last started; scale's default
STAGES = [  # (label, service-name pattern); later stages depend on earlier ones
    ("observability", r"prometheus|grafana|gpu-exporter-farm|alertmanager"),
    ("slurm core", r"slurmctld|mariadb|slurmdbd"),
    ("slurm services", r"slurmrestd|accounting-init"),
    ("mocks", r"bmc-farm|bcm-mock|jira-mock"),
    ("jira real", r"jira-db|jira-real"),
    ("teleport", r"teleport"),
    ("kubernetes", r"k8s-etcd|k8s-apiserver|k8s-controller-manager|k8s-scheduler"),
    ("kubernetes nodes", r"kwok-controller"),  # starts only once the stages exist: with none it exits
    ("bmc addresses", r"bmc-(cn|gpu)\d+"),
    ("nodes", r"(cn|gpu|rgpu)\d+"),
    ("login node", r"login\d*"),
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


def build_targets(services: dict, names: list[str]) -> list[str]:
    """One service per distinct image to build: the nodes share an image, so building every name would repeat the work."""
    seen, out = set(), []
    for n in names:
        svc = services.get(n) or {}
        key = (svc.get("build"), svc.get("image"))
        if svc.get("build") and key not in seen:
            seen.add(key)
            out.append(n)
    return out


STAMPS = "build-stamps.json"  # next to the generated compose file (gitignored): {service: {"hash": ..., "image": ...}}
FORCE_BUILD = "WORKBENCH_FORCE_BUILD"
GPU_FARM = ("gpu-exporter-farm", "../../components/gpu-exporter-farm", "workbench/gpu-exporter-farm")  # lives in compose/observability.yml


def context_hash(ctx: Path, base: str = "") -> str | None:
    """sha256 over every file of a build context (paths and bytes), plus the hash of the image it builds FROM. None when the context is missing."""
    if not ctx.is_dir():
        return None
    h = hashlib.sha256(base.encode())
    for f in sorted(p for p in ctx.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        h.update(f.relative_to(ctx).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def _stamps(path: Path) -> dict:
    try:
        d = json.loads((path.parent / STAMPS).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_stamps(path: Path, stamps: dict) -> None:
    try:
        (path.parent / STAMPS).write_text(json.dumps(stamps, indent=1))
    except OSError:
        pass  # a missing stamp only means the next up builds again


def image_hashes(names: list[str], services: dict, path: Path, base_name: str | None) -> dict[str, tuple[str | None, str | None]]:
    """{service: (inputs hash, image tag)}. Images built FROM the node base also hash the base, so a base change rebuilds them."""
    def ctx_of(n: str) -> Path | None:
        if n == GPU_FARM[0]:
            return path.parent / GPU_FARM[1]
        b = (services.get(n) or {}).get("build")
        return path.parent / b if b else None
    base_ctx = ctx_of(base_name) if base_name else None
    base_hash = (context_hash(base_ctx) or "") if base_ctx else ""
    out = {}
    for n in names:
        ctx = ctx_of(n)
        derived = bool(ctx and (ctx / "Dockerfile").is_file() and "FROM workbench/node-base" in (ctx / "Dockerfile").read_text()) and n != base_name
        out[n] = (context_hash(ctx, base_hash if derived else "") if ctx else None, GPU_FARM[2] if n == GPU_FARM[0] else (services.get(n) or {}).get("image"))
    return out


def image_id(image: str, runner: "Runner") -> str:
    """The image's id, or "" when there is none (or the daemon cannot be asked): a stamp is only valid for the exact image it was made from."""
    r = runner(["docker", "image", "inspect", "--format", "{{.Id}}", image])
    return r.stdout.strip() if r.returncode == 0 else ""


def stale_images(hashes: dict, stamps: dict, runner: "Runner", force: bool) -> list[str]:
    """Targets whose build inputs changed, that were never stamped, whose image is gone, or all of them when forced."""
    def fresh(n: str) -> bool:
        h, img = hashes[n]
        st = stamps.get(n, {})
        return bool(h and img and st.get("hash") == h and st.get("id") and image_id(img, runner) == st["id"])
    return list(hashes) if force else [n for n in hashes if not fresh(n)]


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
        say(errors.render("WB-REFUSED-GOVERNOR", str(r.get("error")), f"REFUSED by governor: {r.get('error')}"))
    return bool(r.get("ok"))


def missing_mb(services: dict, runner: Runner, file: Path) -> int:
    """Expected memory (measured use plus tolerance, never above the cap) of the services not running yet: what a start would add."""
    up_now = set(runner(dc("ps", "--status", "running", "--format", "{{.Service}}", file=file)).stdout.split())
    new = [n for n in services if n not in up_now]
    return sum(memory.expected_mb(n, int(services[n]["mem_limit"].rstrip("m")), services[n].get("labels")) for n in new) + memory.overhead_mb(len(new))[0]


def admit(profile: str, available: int | None = None) -> tuple[bool, str]:
    """Pre-flight: refuse unless the profile fits, naming a smaller profile that does."""
    avail = checks.mem_available() if available is None else available
    app = memory.app_mb() / 1024  # the app under test's reserve, on top of every profile's own expected use
    return checks.preflight(profile, {n: b + app for n, b in checks.load_profiles().items()}, avail)


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


def _answering(names: list[str], runner: Runner, path: Path, wait: float) -> list[str]:
    """The services among `names` that are running but do not answer yet (issue 449; benchctl/ready.py)."""
    return ready.wait_ready(names, runner, lambda *a: dc(*a, file=path), wait)


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


STAGE_ENV = "WORKBENCH_STAGE_LINES"  # the governor sets it: `up` then prints one `@stage {json}` line per stage change (issue 519)


def stage_lines(event: dict) -> None:
    """Default stage reporter: machine-readable lines for the governor, and nothing for a person at a terminal."""
    if os.environ.get(STAGE_ENV) == "1":
        print("@stage " + json.dumps(event), flush=True)


def stage_plan(names: list[str], todo: dict, has_images: bool, restart: bool) -> list[str]:
    """The ordered stages `up` will report, as the page shows them."""
    return (["Build images"] if has_images else []) + [label.capitalize() for label, _ in stage_services(names)] + (["Restart slurmctld"] if restart else [])


def up(sim: dict, profile: str, runner: Runner = _run, say: Callable[[str], None] = print,
       available: int | None = None, wait: float = 120, prof: dict | None = None,
       stage: Callable[[dict], None] = stage_lines) -> bool:
    """Start the profile. `prof` is a profile built from a workbench selection (benchctl apply): admitted by its own
    estimate instead of the named profile's budget."""
    chosen, began, stage_secs = prof, time.monotonic(), []
    prof = prof if prof is not None else compose.load_profile(profile)
    try:
        data = compose.generate(sim, prof)
    except logins.LoginError as e:
        say(f"FAILED login nodes: {e}")
        return False
    except topology.TopologyError as e:
        say(f"FAILED topology: {e}")
        return False
    except compose.JobMemoryError as e:
        say(f"FAILED node memory: {e}")
        return False
    except qos.QosError as e:
        say(f"FAILED qos: {e}")
        return False
    if slurmconf.test_timers_on():
        say(f"NOTE test timers are on ({slurmconf.TEST_TIMERS_ENV}=1): {slurmconf.timers()}; not for client testing")
    conf = COMPOSE_FILE.parent / "slurm" / "slurm.conf"  # a changed file needs a controller restart (it only rereads at start)
    topo = conf.with_name("topology.conf")  # the switches are read at start too (issue 543)
    def conf_text():
        return (conf.read_text() if conf.is_file() else None, topo.read_text() if topo.is_file() else None)
    old_conf = conf_text() if conf.is_file() else None
    path = compose.write(data, sim=sim, compute_nodes=prof.get("compute_nodes"))
    conf_changed = old_conf is not None and conf.is_file() and conf_text() != old_conf
    skip = unbuildable(data["services"], path.parent)
    todo = {n: v for n, v in data["services"].items() if n not in skip}
    if (problem := network_problem(runner)):
        say(errors.render("WB-REFUSED-NETWORK", problem, f"REFUSED: {problem}"))
        return False
    missing = missing_mb(todo, runner, path)
    if missing:  # a running stack is already in "used" memory: nothing to admit when nothing starts
        if chosen is None:
            ok, msg = admit(profile, available)
        else:
            avail = checks.mem_available() if available is None else available
            ok, msg = workbench.verdict(workbench.estimate(sim, chosen, running=workbench.running_services()), avail // (1024 * 1024))
        say(("admitted: " + msg) if ok else errors.render("WB-REFUSED-MEMORY", msg, "REFUSED: " + msg))
        if not ok or not governor_admits(missing, say):
            return False
    else:
        say(f"admitted: {profile}: every service is already running")
    PROFILE_FILE.write_text(profile)
    if skip:
        say(f"skipping (not implemented yet): {', '.join(skip)}")
    if "bmc-farm" in todo:
        ensure_bmc_credentials()
    if any(re.fullmatch(r"slurm(ctld|restd|dbd)|accounting-init|(cn|gpu|rgpu)\d+", n) or logins.is_login(n) for n in todo):
        try:
            ensure_slurm_keys()
        except OSError as e:
            say(f"FAILED slurm keys: {e}")
            return False
    if "jira-real" in todo:
        jira_real.ensure_secrets()
    if any(logins.is_login(n) for n in todo) or any(re.fullmatch(r"(cn|gpu|rgpu)\d+", n) for n in todo):
        try:
            ensure_ssh_keys()
        except OSError as e:
            say(f"FAILED login node keys: {e}")
            return False
    names = [n for n in data["services"] if n not in skip] + ["prometheus", "grafana", "gpu-exporter-farm", "alertmanager"]
    node_re = r"(cn|gpu|rgpu)\d+"
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
    restart = bool((changed or acct_added or (conf_changed and "slurmctld" in before)) and "slurmctld" in todo)
    first = next((n for n in names if re.fullmatch(r"(cn|gpu|rgpu)\d+", n)), None)
    stage({"plan": stage_plan(names, todo, first is not None or bool(build_targets(todo, names)), restart)})
    if first is not None or build_targets(todo, names):
        stage({"stage": "Build images", "state": "running"})
    # slurmctld, slurmrestd and login build FROM node-base's tag, which only a node service rebuilds. Build it first,
    # or a changed base leaves the controller on the old image (the keys are mounted now, issue 465, but the entrypoint is in the base).
    # Build stamp (issue 576): an image whose build inputs hash matches the last good build, and that still exists, is not built again;
    # a changed mock or entrypoint changes the hash, so issue 449's guarantee stays. WORKBENCH_FORCE_BUILD=1 builds everything.
    wanted = ([first] if first is not None else []) + [t for t in build_targets(todo, names) if t != first] + [GPU_FARM[0]]  # gpu-exporter-farm is in compose/observability.yml, not in the generated services (#462): built like every other image
    stamps, hashes = _stamps(path), image_hashes(wanted, data["services"], path, first)
    stale = stale_images(hashes, stamps, runner, bool(os.environ.get(FORCE_BUILD)))
    built_now: list[str] = []
    t0 = time.monotonic()
    # slurmctld, slurmrestd and login build FROM node-base's tag, which only a node service rebuilds. Build it first,
    # or a changed base leaves the controller on the old image (the keys are mounted now, issue 465, but the entrypoint is in the base).
    if first in stale:
        r = runner(dc("build", first, file=path))
        if r.returncode != 0:
            say(f"FAILED node base image: {r.stderr.strip()[-300:]}")
            stamps.pop(first, None)
            _save_stamps(path, stamps)
            return False
        built_now.append(first)
    targets = [t for t in stale if t != first]  # the node base was built above; build once (issue 449): the per-stage `up` calls below no longer build
    if targets:
        say(f"building images: {', '.join(targets)}")
        r = runner(dc("build", *targets, file=path))
        if r.returncode != 0:
            say(f"FAILED image build: {r.stderr.strip()[-300:]}")
            for t in targets:
                stamps.pop(t, None)
            _save_stamps(path, stamps)
            return False
        built_now += targets
    for n in built_now:
        iid = image_id(hashes[n][1], runner) if hashes[n][1] else ""
        if hashes[n][0] and iid:
            stamps[n] = {"hash": hashes[n][0], "image": hashes[n][1], "id": iid}
        else:
            stamps.pop(n, None)
    if built_now:
        _save_stamps(path, stamps)
    say(f"images: {len(wanted) - len(built_now)} unchanged" + (f", built {', '.join(built_now)} in {time.monotonic() - t0:.0f} s" if built_now else ""))
    if first is not None or wanted:
        stage({"stage": "Build images", "state": "done"})
    ips = addressing.plan(sim, prof.get("compute_nodes"))
    if "teleport" in todo:
        try:
            teleport.prepare(sim, ips)
        except (OSError, teleport.TeleportError) as e:
            say(f"FAILED teleport setup: {e}")
            return False
    if dcgm.SERVICE in todo and (bad := dcgm.problems(sim)):
        say(f"FAILED dcgm: {bad[0]}")
        return False
    if real_gpu.SERVICE in todo and (bad := real_gpu.problems()):
        say(f"FAILED real-gpu: {bad[0]}")
        return False
    dcgm.prepare(COMPOSE_FILE.parent, sim)  # the farm mounts this directory whether or not the part is on
    if any(n in todo for n in k8s.SERVICES):
        if bad := k8s.problems(sim):
            say(f"FAILED kubernetes: {bad[0]}")
            return False
        try:
            k8s.prepare(sim, ips)
        except (OSError, k8s.K8sError) as e:
            say(f"FAILED kubernetes setup: {e}")
            return False
    if promtargets.prepare(sim, set(todo), prof.get("compute_nodes"), COMPOSE_FILE.parent):  # the jobs of the selected parts (ADR 0073)
        runner(["docker", "kill", "-s", "HUP", f"{PROJECT}-prometheus-1"])  # a running Prometheus re-reads them; one that is not running reads them at its start
    for label, svcs in stage_services(names):
        say(f"starting {label}: {', '.join(svcs)}")
        stage({"stage": label.capitalize(), "state": "running"})
        stage_began = time.monotonic()
        r = runner(dc("up", "-d", "--no-deps", *svcs, file=path))
        if r.returncode != 0:
            say(f"FAILED {label}: {r.stderr.strip()[-300:]}")
            return False
        if (late := wait_running(svcs, runner, wait)):
            say(f"FAILED {label}: not running: {', '.join(late)}")
            return False
        if (late := _answering(svcs, runner, path, wait)):
            say(f"FAILED {label}: running but not answering: {', '.join(late)}")
            return False
        if label == "teleport" and not teleport.bootstrap(sim, ips, runner, dc, say):  # before the nodes read their certificates
            return False
        if label == "teleport" and controller.enabled(sim):  # slurmctld started in an earlier stage, before its host certificate existed
            runner(dc("exec", "-T", "slurmctld", "sh", "-c", "/usr/local/sbin/teleport-ssh && pkill -HUP -x sshd", file=path))  # trust the CA, then reload sshd
        if label == "kubernetes" and not k8s.bootstrap(sim, ips, say):
            return False  # the cluster content (stages, nodes, fixtures) must exist before KWOK starts
        if label == "teleport":
            teleport.snapshot()  # the identity survives a rebuild (an emptied data directory restores it)
        stage_secs.append(f"{label} {time.monotonic() - stage_began:.0f}")
        stage({"stage": label.capitalize(), "state": "done"})
    if restart:  # slurm.conf changed under a running controller
        stage({"stage": "Restart slurmctld", "state": "running"})
        say("restarting slurmctld to pick up the new " + ("accounting setup" if acct_added and not changed else "node list" if changed else "slurm.conf"))
        r = runner(dc("up", "-d", "--no-deps", "--force-recreate", "slurmctld", file=path))
        if r.returncode != 0 or wait_running(["slurmctld"], runner, wait) or _answering(["slurmctld"], runner, path, wait):
            say(f"FAILED slurmctld restart: {r.stderr.strip()[-300:]}")
            return False
        stage({"stage": "Restart slurmctld", "state": "done"})
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
        say(f"ssh: ssh -i {SSH_DIR}/id_ed25519 -o IdentitiesOnly=yes -p {compose.SSH_PORT + idx} <user>@127.0.0.1 "
            f"(users from sim.yaml; {lname}, {logins.KIND_LABEL[logins.kinds(sim)[lname]]})")
    for lname in logins.via_teleport(sim) if "login" in todo else []:
        say(f"{lname}: reachable through Teleport only: tsh --proxy={teleport.proxy_addr(sim)} --insecure -i "
            f"{teleport.SECRETS_DIR}/identity/<user> ssh <user>@{lname}")
    if "slurmctld" in todo and controller.enabled(sim) and "teleport" in todo:
        say(f"controller: through Teleport too: tsh --proxy={teleport.proxy_addr(sim)} --insecure -i {teleport.SECRETS_DIR}/identity/<user> ssh <user>@{controller.ALIAS} "
            "(only the whitelisted users get a shell)")
    if "teleport" in todo and (warn := teleport.tsh_problem(sim)):
        say(f"WARN {warn}")
    if "teleport" in todo:
        say(f"teleport: https://{teleport.proxy_addr(sim)} (self-signed; name {teleport.DNS_NAME}, fallback 127.0.0.1:{teleport.PROXY_PORT}); "
            f"identity files in {teleport.SECRETS_DIR}/identity; password and one-time code for tsh login: ./scripts/benchctl add invite <user>")
        say(f"teleport name on this machine (optional, needs sudo, run it yourself): echo '{teleport.proxy_ip(sim)} {teleport.DNS_NAME}' | sudo tee -a /etc/hosts")
    if "k8s-apiserver" in todo:
        say(f"kubernetes: https://{k8s.api_ip(ips)}:{k8s.API_PORT} (127.0.0.1:{k8s.API_PORT} as a fallback); read-only kubeconfigs "
            f"{k8s.SECRETS_DIR}/viewer.kubeconfig (cluster-wide) and limited.kubeconfig (namespace team-a); "
            f"nodes {', '.join(n for n, _ in k8s.node_list(sim))}")
    say(f"stack is up in {time.monotonic() - began:.0f} s ({', '.join(stage_secs)})")
    return True


def down(runner: Runner = _run, say: Callable[[str], None] = print) -> bool:
    """Stop and remove only this compose project; the user's own containers/VMs are untouched."""
    t = os.environ.get("WORKBENCH_STOP_TIMEOUT", "")  # seconds before docker kills a container that ignores SIGTERM (default 10; the tests set 2, issue 310)
    r = runner(dc("down", "--remove-orphans", *(["-t", t] if t.isdigit() else [])))
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
    for name in ("ssh_host_ed25519_key", "ssh_host_node_ed25519_key", "ssh_host_jump_ed25519_key", "id_ed25519", "id_cluster"):
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


SLURM_KEYS = Path("compose/generated/secrets/slurm")


def ensure_slurm_keys(path: Path = SLURM_KEYS) -> Path:
    """The munge key and the Slurm JWT signing key, made once per checkout and mounted read-only into every Slurm container
    (issue 465): no key material lives in an image layer, and no two clones share keys. The container's entrypoint (root)
    installs them with the owners munge and slurmctld need, so the host files can stay 0600 and owned by you.

    Kept across `up`s: a new key would break every running node until it restarts.
    """
    import secrets

    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, size in (("munge.key", 1024), ("jwt_hs256.key", 32)):
        if not (path / name).exists():
            fd = os.open(path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(secrets.token_bytes(size))
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
