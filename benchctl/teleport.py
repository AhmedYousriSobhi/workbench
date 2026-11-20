"""Teleport access to the nodes (SPEC C-16, ADR 0020, issue 39).

One container runs Teleport's auth and proxy services. Nodes keep their own `sshd` (agentless OpenSSH mode): the
proxy asks the Teleport CA for a short-lived user certificate, `sshd` trusts only that CA, and the proxy dials the
node's address. Registration needs no Teleport binary on a node: a `kind: node, sub_kind: openssh` resource per node
(created with `tctl`), the CA public key from the proxy, and a host certificate per node from `tctl auth sign`.

Everything here is derived from sim.yaml and the addressing plan; `bootstrap` runs after the container is up and
before the nodes start, because each node's sshd reads its host certificate when it starts."""
import json
import re
import shutil
import sqlite3
import ssl
import time
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

import yaml

from . import addressing, controller, k8s, logins

IMAGE = "public.ecr.aws/gravitational/teleport-distroless"
DEFAULT_VERSION = "18.11.8"   # a Community Edition tag; licence note in ADR 0020. Teleport supports a tsh one major behind it.
DEFAULT_TTL = "12h"
CLUSTER = "workbench"
KUBE_PORT = 3027               # the Kubernetes service listens here inside the container (not published; clients use the web port)
DIAG_PORT = 3434               # diagnostics (/healthz, /metrics), published on 127.0.0.1 only; the page reads it for status and traffic dots
PROXY_PORT = 3080              # web UI, the API and (single-port mode, config v3) the SSH proxy
DNS_NAME = "teleport.workbench.test"  # a container alias on the cluster network; `.test` is reserved (RFC 6761), so it never clashes with a real name
ROLE = "workbench-user"
KUBE_GROUP = "workbench-k8s-viewers"  # the viewer tier's Kubernetes group; bound to the read-only role in k8s/fixtures/47-rbac-teleport.yaml
TIERS = ("viewer", "developer", "admin")  # Kubernetes access tiers (ADR 0084 W1): one Teleport role and one Kubernetes group each
DEFAULT_TIER = "viewer"
DEV_NAMESPACE = "team-a"               # the developer tier writes here and nowhere else
TIER_GROUPS = {"viewer": KUBE_GROUP, "developer": "workbench-k8s-developers", "admin": "workbench-k8s-admins"}
ROLE_VERSION = "v8"                    # `kubernetes_resources` with plural kinds and api_group (Teleport roles reference)
KUBECONFIG = "kube.kubeconfig"        # under the secrets folder (mounted at CONTAINER_SECRETS): the static kubeconfig of the Kubernetes Service
CONF_DIR = Path("compose/generated/teleport")             # mounted read-only at /etc/teleport; data/ is writable
SECRETS_DIR = Path("compose/generated/secrets/teleport")  # CA key, host certificates, identity files (never tracked)
CONTAINER_SECRETS = "/run/workbench/teleport"
PROXY_CERT = "webproxy_cert.pem"  # in data/: the proxy's self-signed certificate, made by Teleport at its first start (no https_keypairs)
PINNED_CA = "proxy_ca.pem"        # its copy under SECRETS_DIR, the only certificate benchctl trusts on the proxy port (issue 864)
_TTL = re.compile(r"[1-9][0-9]*[smh]")
_URL = re.compile(r"https://\S+/web/(?:invite|reset)/\S+")


class TeleportError(Exception):
    pass


def settings(sim: dict) -> dict:
    """The `teleport:` block of sim.yaml with defaults filled in, validated."""
    t = {"version": DEFAULT_VERSION, "cert_ttl": DEFAULT_TTL, **(sim.get("teleport") or {})}
    t["version"], t["cert_ttl"] = str(t["version"]), str(t["cert_ttl"])
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", t["version"]):
        raise TeleportError(f"teleport.version must look like 18.11.8, not {t['version']!r}")
    if not _TTL.fullmatch(t["cert_ttl"]):
        raise TeleportError(f"teleport.cert_ttl must look like 30m or 12h, not {t['cert_ttl']!r}")
    tiers = t.get("kube_tier") or {}
    names = {u["name"] for u in sim.get("users", [])}
    if not isinstance(tiers, dict):
        raise TeleportError("teleport.kube_tier must map a user to a tier, e.g. {bob: developer}")
    for user, tier in tiers.items():
        if user not in names:
            raise TeleportError(f"teleport.kube_tier names {user!r}, which is not in users: of sim.yaml")
        if tier not in TIERS:
            raise TeleportError(f"teleport.kube_tier for {user} must be one of {', '.join(TIERS)}, not {tier!r}")
    t["kube_tier"] = dict(tiers)
    return t


def tier_of(sim: dict, user: str) -> str:
    """The Kubernetes access tier of a user (`teleport.kube_tier` in sim.yaml); viewer when not listed."""
    return settings(sim)["kube_tier"].get(user, DEFAULT_TIER)


def tier_role(tier: str) -> str:
    return f"workbench-kube-{tier}"


def user_roles(sim: dict, user: str, kube: bool) -> list[str]:
    return [ROLE, tier_role(tier_of(sim, user))] if kube else [ROLE]


def tier_roles() -> list[dict]:
    """One role per tier, as `tctl create` documents. The Teleport layer (`kubernetes_resources`) and the Kubernetes layer (the group's
    binding in k8s/fixtures/47-rbac-teleport.yaml) both limit the namespace: Teleport's docs say the two must line up."""
    scope = {"viewer": ("*", ["*"]), "developer": (DEV_NAMESPACE, ["*"]), "admin": ("*", ["*"])}
    docs = []
    for tier in TIERS:
        ns, verbs = scope[tier]
        docs.append({"kind": "role", "version": ROLE_VERSION, "metadata": {"name": tier_role(tier)},
                     "spec": {"allow": {"kubernetes_labels": {"*": "*"}, "kubernetes_groups": [TIER_GROUPS[tier]],
                                        "kubernetes_resources": [{"kind": "*", "api_group": "*", "namespace": ns, "name": "*", "verbs": verbs}]}}})
    return docs


_TSH = re.compile(r"Teleport v(\d+)\.(\d+)\.(\d+)")


def tsh_version(runner: Callable | None = None) -> tuple[int, int, int] | None:
    """The version of the `tsh` on this machine (`tsh version --client`, no network), or None when there is none or it says nothing readable."""
    import subprocess
    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False))
    try:
        r = run(["tsh", "version", "--client"])
    except (OSError, subprocess.SubprocessError):
        return None
    m = _TSH.search((r.stdout or "") + (r.stderr or "")) if r.returncode == 0 else None
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def tsh_problem(sim: dict, runner: Callable | None = None) -> str | None:
    """One line when the local tsh is a newer major version than the Teleport server (it supports a client one major behind, not ahead,
    and a newer one fails with an unclear error); None when it is fine or there is no tsh (issue 210)."""
    have, want = tsh_version(runner), settings(sim)["version"]
    if have is None or have[0] <= int(want.split(".")[0]):
        return None
    return (f"tsh {'.'.join(map(str, have))} on this machine is newer than the Teleport server {want} and will be refused: "
            f"install tsh {want.split('.')[0]}.x or earlier, or set teleport.version in sim.yaml to {have[0]}.x")


def image(sim: dict) -> str:
    return f"{IMAGE}:{settings(sim)['version']}"


def config(sim: dict, kube: bool = False) -> str:
    """teleport.yaml for auth + proxy only (no ssh_service: nodes are agentless). `proxy_listener_mode: multiplex` (auth_service) serves
    everything on one port, so only 3080 is published; without it tsh finds no SSH proxy address. A self-signed certificate (no https_keypairs), local users with
    one-time codes (OTP): Teleport 18 cannot turn MFA off (`second_factor: off` makes auth exit with "cannot disable
    multi-factor authentication"), and `second_factors` is its documented setting. Identity files need no code.
    `kube` (the k8s part is selected, ADR 0083): adds a `kubernetes_service` in the same process, registering the simulated cluster from a static
    kubeconfig; clients reach it on the same web port (config v3 multiplexes), so no port is added."""
    settings(sim)
    conf = {"version": "v3",
            "teleport": {"nodename": "teleport", "data_dir": "/var/lib/teleport", "diag_addr": f"0.0.0.0:{DIAG_PORT}",
                         "log": {"output": "stderr", "severity": "INFO"}},
            "auth_service": {"enabled": True, "cluster_name": CLUSTER, "listen_addr": "0.0.0.0:3025",
                             "proxy_listener_mode": "multiplex",  # tells tsh to use the web port for SSH too (ping said tls_routing false without it)
                             "authentication": {"type": "local", "second_factors": ["otp"]}},
            "ssh_service": {"enabled": False},
            "proxy_service": {"enabled": True, "web_listen_addr": f"0.0.0.0:{PROXY_PORT}",
                              "public_addr": [proxy_addr(sim), f"{DNS_NAME}:{PROXY_PORT}"]}}  # the address and the name clients use, in the certificate and in links
    if kube:
        # Teleport 18 refuses a kubernetes_service in this process without `listen_addr` or a proxy to dial out to (found live: "set
        # kubernetes_service.listen_addr if this process can be reached from a teleport proxy"). The listener stays inside the container:
        # nothing publishes 3027, so clients still use only the web port.
        conf["kubernetes_service"] = {"enabled": True, "listen_addr": f"0.0.0.0:{KUBE_PORT}", "kubeconfig_file": f"{CONTAINER_SECRETS}/{KUBECONFIG}",
                                      "labels": {"env": "workbench"}}
    return yaml.safe_dump(conf, sort_keys=False)


def proxy_ip(sim: dict) -> str:
    """The proxy's fixed private address (ADR 0018): what clients dial, instead of a published localhost port."""
    return addressing.plan(sim)["services"]["teleport"]


def proxy_addr(sim: dict) -> str:
    return f"{proxy_ip(sim)}:{PROXY_PORT}"


def node_id(name: str) -> str:
    """A stable UUID per node name: the resource name Teleport wants for an OpenSSH node, and part of its host
    certificate, so it must not change between runs."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{name}.{CLUSTER}.workbench"))


def targets(sim: dict, ips: dict) -> dict[str, str]:
    """node name -> address of everything a user can reach through Teleport: the login nodes whose mode says so
    (`teleport` or `both`, ADR 0023), the controller when there is one (issue 416: its sshd lets only the whitelisted users in,
    so Teleport adds a way in, not a wider one) and every node."""
    ctl = {controller.ALIAS: ips["services"][controller.CONTROLLER]} if controller.enabled(sim) else {}
    return {n: ips["logins"][n] for n in logins.via_teleport(sim)} | ctl | dict(ips["nodes"])


def resources(sim: dict, ips: dict, kube: bool = False) -> str:
    """The roles, the nodes as one multi-document YAML for `tctl create -f`. `workbench-user` is only the SSH access (logins, nodes); with `kube`
    three more roles carry the Kubernetes tiers (viewer, developer, admin; ADR 0084 W1) and a user gets the one `teleport.kube_tier` names.
    Roles are v8; a user's groups are the union of its roles, so no tier is added to the base role."""
    t = settings(sim)
    users = [u["name"] for u in sim.get("users", [])]
    docs = [{"kind": "role", "version": ROLE_VERSION, "metadata": {"name": ROLE},
             "spec": {"options": {"max_session_ttl": t["cert_ttl"]}, "allow": {"logins": users, "node_labels": {"*": "*"}}}}]
    if kube:
        docs += tier_roles()
    for name, ip in targets(sim, ips).items():
        kind = "login" if logins.is_login(name) else "controller" if name == controller.ALIAS else "gpu" if name.startswith("gpu") else "compute"
        docs.append({"kind": "node", "version": "v2", "sub_kind": "openssh",
                     "metadata": {"name": node_id(name), "labels": {"kind": kind}},
                     "spec": {"addr": f"{ip}:22", "hostname": name}})
    return yaml.safe_dump_all(docs, sort_keys=False)


def user_resource(name: str, roles: list[str] | None = None) -> str:
    """One user as its own file: a user is created only when it does not exist (`bootstrap`), because `tctl create -f` replaces a user
    and with it the password and one-time code chosen from an invite."""
    return yaml.safe_dump({"kind": "user", "version": "v2", "metadata": {"name": name}, "spec": {"roles": roles or [ROLE]}}, sort_keys=False)


STATE_DIR = "state"  # under the secrets folder: a copy of Teleport's own state that survives an emptied data directory
STATE_FILES = ("host_uuid", "webproxy_cert.pem", "webproxy_key.pem")


def snapshot(conf: Path = CONF_DIR, secrets: Path = SECRETS_DIR) -> bool:
    """Copy the cluster's identity (CA, users, passwords, one-time codes live in the sqlite backend) to the secrets folder, so
    a rebuilt workbench can be the same cluster. The sqlite backup API reads a database Teleport has open. False: nothing to copy."""
    db = conf / "data" / "backend" / "sqlite.db"
    version = conf / "cluster-version"
    if not db.is_file() or not version.is_file():
        return False
    dest = secrets / STATE_DIR
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    src, out = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(dest / "sqlite.db.tmp")
    try:
        src.backup(out)
    finally:
        src.close()
        out.close()
    (dest / "sqlite.db.tmp").replace(dest / "sqlite.db")
    for name in STATE_FILES:
        if (conf / "data" / name).is_file():
            shutil.copyfile(conf / "data" / name, dest / name)
    (dest / "version").write_text(version.read_text())
    return True


def restore(conf: Path, secrets: Path, version: str) -> bool:
    """Put a snapshot of the same Teleport version into an empty data directory. False: none, or another version (a new cluster)."""
    src = secrets / STATE_DIR
    if not (src / "sqlite.db").is_file() or not (src / "version").is_file() or (src / "version").read_text().strip() != version:
        return False
    data = conf / "data"
    (data / "backend").mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(src / "sqlite.db", data / "backend" / "sqlite.db")
    (data / "backend" / "sqlite.db").chmod(0o600)
    for name in STATE_FILES:
        if (src / name).is_file():
            shutil.copyfile(src / name, data / name)
            (data / name).chmod(0o600)
    return True


def reset(conf: Path = CONF_DIR, secrets: Path = SECRETS_DIR) -> list[Path]:
    """Forget the cluster: delete its data and the saved identity, so the next `up` makes a new cluster (new CA, no users'
    passwords). Returns what was removed. The caller makes sure Teleport is stopped: `down` saves the identity again."""
    gone = [p for p in (conf / "data", secrets / STATE_DIR) if p.exists()]
    for p in gone:
        shutil.rmtree(p)
    return gone


def prepare(sim: dict, ips: dict, conf: Path = CONF_DIR, secrets: Path = SECRETS_DIR, kube: bool = False,
            kube_dir: Path | None = None) -> bool:
    """Write the config and resource files and make the directories the container writes to (owned by this user:
    Docker would create them as root, and the container runs as the invoking user). With `kube` also the Kubernetes Service's kubeconfig
    (from the k8s credentials, so `k8s.prepare` runs first). Returns True when the config or that kubeconfig differs from what a running
    Teleport read at its start: the caller then recreates the container (it reads both only at start)."""
    data = conf / "data"
    fresh = not data.is_dir() or not any(data.iterdir())  # a new cluster has a new CA: what the old one signed is useless
    data.mkdir(parents=True, exist_ok=True)
    (secrets / "hosts").mkdir(parents=True, exist_ok=True, mode=0o700)
    version = settings(sim)["version"]
    if fresh and restore(conf, secrets, version):  # the same cluster again: same CA, so certificates, sessions and passwords still hold
        fresh = False
    (conf / "cluster-version").write_text(version + "\n")
    if fresh:
        for old in (*(secrets / "hosts").iterdir(), secrets / "openssh_ca.pub"):
            old.unlink(missing_ok=True)
    (secrets / "identity").mkdir(exist_ok=True, mode=0o700)
    kube_text = k8s.teleport_kubeconfig(ips, kube_dir) if kube else None
    old = [(p.read_text() if p.is_file() else None) for p in (conf / "teleport.yaml", secrets / KUBECONFIG)]
    (conf / "teleport.yaml").write_text(config(sim, kube))
    if kube_text is not None:
        (secrets / KUBECONFIG).write_text(kube_text)
        (secrets / KUBECONFIG).chmod(0o600)
    else:
        (secrets / KUBECONFIG).unlink(missing_ok=True)
    (conf / "resources.yaml").write_text(resources(sim, ips, kube))
    changed = old != [(conf / "teleport.yaml").read_text(), kube_text]
    for u in (u["name"] for u in sim.get("users", [])):
        (conf / f"user-{u}.yaml").write_text(user_resource(u, user_roles(sim, u, kube)))
    return changed


def _tctl(runner: Callable, dc: Callable, *args: str):
    return runner(dc("exec", "-T", "teleport", "tctl", *args))


def wait_ready(runner: Callable, dc: Callable, timeout: float = 90, sleep: Callable[[float], None] = time.sleep) -> bool:
    for _ in range(max(1, int(timeout // 2))):   # counted, not timed, so an injected sleep keeps tests instant
        if _tctl(runner, dc, "status").returncode == 0:
            return True
        sleep(2)
    return False


_OURS = re.compile(r"(login[0-9]*|controller|cn[0-9]+|gpu[0-9]+)")  # only names this tool registers are ever removed


def prune(sim: dict, ips: dict, runner: Callable, dc: Callable, secrets: Path = SECRETS_DIR) -> list[str]:
    """Forget what the selection no longer has: a login node or compute node that was dropped stays registered in Teleport (`tsh ls`
    lists it, a dial fails with `no route to host`), and keeps its host certificate. Returns the names removed. Never fails `up`:
    an unreadable list just removes nothing."""
    keep = {node_id(n): n for n in targets(sim, ips)}
    removed: list[str] = []
    r = _tctl(runner, dc, "get", "nodes", "--format=json")
    try:
        found = json.loads(r.stdout or "[]") if r.returncode == 0 else []
    except ValueError:
        found = []
    for item in found if isinstance(found, list) else []:
        meta, spec = item.get("metadata", {}), item.get("spec", {})
        host = str(spec.get("hostname", ""))
        stale = meta.get("name") not in keep and item.get("sub_kind") == "openssh" and _OURS.fullmatch(host)
        if stale and _tctl(runner, dc, "rm", f"node/{meta['name']}").returncode == 0:
            removed.append(host)
    hosts = secrets / "hosts"
    for f in hosts.glob("*-cert.pub") if hosts.is_dir() else []:
        name = f.name.removesuffix("-cert.pub")
        if name not in set(keep.values()) and _OURS.fullmatch(name):
            f.unlink(missing_ok=True)
            (hosts / name).unlink(missing_ok=True)
            (hosts / f"{name}.pub").unlink(missing_ok=True)
    return removed


def pin_proxy_cert(conf: Path | None = None, secrets: Path | None = None, sleep: Callable[[float], None] = time.sleep,
                   tries: int = 15) -> Path:
    """Copy the proxy's self-signed certificate from Teleport's data folder (on disk, written by the container itself, never read
    over the network) to the secrets folder, where `proxy_context` pins it. With no `https_keypairs` Teleport makes this certificate
    at its first start (CA:TRUE, names: the private IP, `teleport.workbench.test`, 127.0.0.1); it can appear a moment after
    `tctl status` answers, so a few tries."""
    src, dst = (conf or CONF_DIR) / "data" / PROXY_CERT, (secrets or SECRETS_DIR) / PINNED_CA
    for i in range(tries):
        try:
            text = src.read_text()
        except OSError:
            text = ""
        if "BEGIN CERTIFICATE" in text:
            dst.write_text(text)
            return dst
        if i < tries - 1:
            sleep(1)
    raise TeleportError(f"Teleport has not written its proxy certificate {src}: run `benchctl down`, then `benchctl up --with teleport`")


def proxy_context(secrets: Path | None = None) -> ssl.SSLContext:
    """A client context that trusts only the pinned proxy certificate and checks the name (ADR 0020, issue 864): anything else
    answering on the proxy port is refused."""
    ca = (secrets or SECRETS_DIR) / PINNED_CA
    if not ca.is_file():
        raise TeleportError(f"the pinned Teleport proxy certificate {ca} is missing: run `benchctl up --with teleport`")
    return ssl.create_default_context(cafile=str(ca))  # CERT_REQUIRED and check_hostname stay on


def _cert_refused(e: BaseException) -> bool:
    return isinstance(e, ssl.SSLCertVerificationError) or isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError)


def fetch_ca(port: int = PROXY_PORT, opener: Callable | None = None, conf: Path | None = None, secrets: Path | None = None,
             sleep: Callable[[float], None] = time.sleep) -> str:
    """The OpenSSH user CA public key from the proxy (Teleport's documented source for `TrustedUserCAKeys`), over TLS verified
    against the pinned proxy certificate."""
    if opener is None:
        pin_proxy_cert(conf, secrets, sleep)
        ctx = proxy_context(secrets)
        opener = lambda url: urllib.request.urlopen(url, context=ctx, timeout=10).read().decode()
    try:
        text = opener(f"https://127.0.0.1:{port}/webapi/auth/export?type=openssh").strip()
    except OSError as e:
        if _cert_refused(e):
            raise TeleportError(f"the server on 127.0.0.1:{port} did not present the pinned Teleport proxy certificate "
                                f"({(secrets or SECRETS_DIR) / PINNED_CA}): refused. Check that nothing else uses port {port}, "
                                "then run `benchctl down` and `benchctl up --with teleport`") from e
        raise
    if not text.startswith("cert-authority"):
        raise TeleportError("the proxy did not return an OpenSSH CA key")
    return text.removeprefix("cert-authority ").strip() + "\n"


def bootstrap(sim: dict, ips: dict, runner: Callable, dc: Callable, say: Callable[[str], None] = print,
              secrets: Path = SECRETS_DIR, fetch: Callable[[], str] | None = None,
              sleep: Callable[[float], None] = time.sleep, kube: bool = False) -> bool:
    """Load the role, users and nodes, install the CA key, sign a host certificate for every node that has none,
    and write a fresh identity file per user. Safe to repeat. Returns False after saying why it failed."""
    if not wait_ready(runner, dc, sleep=sleep):
        say("FAILED teleport: the auth service did not become ready (docker logs workbench-teleport-1)")
        return False
    r = _tctl(runner, dc, "create", "-f", "/etc/teleport/resources.yaml")
    if r.returncode != 0:
        say(f"FAILED teleport resources: {r.stderr.strip()[-300:]}")
        return False
    gone = prune(sim, ips, runner, dc, secrets)
    if gone:
        say(f"teleport: forgot {', '.join(sorted(gone))} (no longer in the workbench)")
    for u in (u["name"] for u in sim.get("users", [])):
        if _tctl(runner, dc, "get", f"user/{u}").returncode != 0:  # absent: create it; present: keep its password, only set its roles
            r = _tctl(runner, dc, "create", f"/etc/teleport/user-{u}.yaml")
        else:  # a tier changed in sim.yaml (or the k8s part went on or off): `users update` keeps the password and one-time code
            r = _tctl(runner, dc, "users", "update", u, f"--set-roles={','.join(user_roles(sim, u, kube))}")
        if r.returncode != 0:
            say(f"FAILED teleport user {u}: {r.stderr.strip()[-300:]}")
            return False
    try:
        (secrets / "openssh_ca.pub").write_text((fetch or (lambda: fetch_ca(secrets=secrets, sleep=sleep)))())
    except (OSError, TeleportError) as e:
        say(f"FAILED teleport CA key: {e}")
        return False
    for name, ip in targets(sim, ips).items():
        if (secrets / "hosts" / f"{name}-cert.pub").is_file():
            continue
        r = _tctl(runner, dc, "auth", "sign", f"--host={ip},{name},{node_id(name)}" + (f",{controller.CONTROLLER}" if name == controller.ALIAS else ""), "--format=openssh",
                  f"--out={CONTAINER_SECRETS}/hosts/{name}")
        if r.returncode != 0:
            say(f"FAILED teleport host certificate for {name}: {r.stderr.strip()[-300:]}")
            return False
    ttl = settings(sim)["cert_ttl"]
    for u in (u["name"] for u in sim.get("users", [])):
        r = _tctl(runner, dc, "auth", "sign", f"--user={u}", f"--ttl={ttl}", "--format=file",
                  f"--out={CONTAINER_SECRETS}/identity/{u}", "--overwrite")
        if r.returncode != 0:
            say(f"FAILED teleport identity for {u}: {r.stderr.strip()[-300:]}")
            return False
    return True


def invite(user: str, runner: Callable, dc: Callable) -> str:
    """A one-time link to choose `user`'s password in a browser (needed for `tsh login`; identity files need none)."""
    r = _tctl(runner, dc, "users", "reset", user)
    m = _URL.search(r.stdout + r.stderr)
    if r.returncode != 0 or not m:
        raise TeleportError(f"could not make an invite for {user}: {(r.stderr or r.stdout).strip()[-200:]}")
    return m.group(0)
