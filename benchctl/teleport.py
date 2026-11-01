"""Teleport access to the nodes (SPEC C-16, ADR 0020, issue 39).

One container runs Teleport's auth and proxy services. Nodes keep their own `sshd` (agentless OpenSSH mode): the
proxy asks the Teleport CA for a short-lived user certificate, `sshd` trusts only that CA, and the proxy dials the
node's address. Registration needs no Teleport binary on a node: a `kind: node, sub_kind: openssh` resource per node
(created with `tctl`), the CA public key from the proxy, and a host certificate per node from `tctl auth sign`.

Everything here is derived from sim.yaml and the addressing plan; `bootstrap` runs after the container is up and
before the nodes start, because each node's sshd reads its host certificate when it starts."""
import re
import ssl
import time
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

import yaml

IMAGE = "public.ecr.aws/gravitational/teleport-distroless"
DEFAULT_VERSION = "18.11.8"   # a Community Edition tag; licence note in ADR 0020. Teleport supports a tsh one major behind it.
DEFAULT_TTL = "12h"
CLUSTER = "workbench"
PROXY_PORT = 3080              # web UI, the API and (single-port mode, config v3) the SSH proxy
ROLE = "workbench-user"
CONF_DIR = Path("compose/generated/teleport")             # mounted read-only at /etc/teleport; data/ is writable
SECRETS_DIR = Path("compose/generated/secrets/teleport")  # CA key, host certificates, identity files (never tracked)
CONTAINER_SECRETS = "/run/workbench/teleport"
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
    return t


def image(sim: dict) -> str:
    return f"{IMAGE}:{settings(sim)['version']}"


def config(sim: dict) -> str:
    """teleport.yaml for auth + proxy only (no ssh_service: nodes are agentless). `proxy_listener_mode: multiplex` (auth_service) serves
    everything on one port, so only 3080 is published; without it tsh finds no SSH proxy address. A self-signed certificate (no https_keypairs), local users with
    one-time codes (OTP): Teleport 18 cannot turn MFA off (`second_factor: off` makes auth exit with "cannot disable
    multi-factor authentication"), and `second_factors` is its documented setting. Identity files need no code."""
    settings(sim)
    conf = {"version": "v3",
            "teleport": {"nodename": "teleport", "data_dir": "/var/lib/teleport",
                         "log": {"output": "stderr", "severity": "INFO"}},
            "auth_service": {"enabled": True, "cluster_name": CLUSTER, "listen_addr": "0.0.0.0:3025",
                             "proxy_listener_mode": "multiplex",  # tells tsh to use the web port for SSH too (ping said tls_routing false without it)
                             "authentication": {"type": "local", "second_factors": ["otp"]}},
            "ssh_service": {"enabled": False},
            "proxy_service": {"enabled": True, "web_listen_addr": f"0.0.0.0:{PROXY_PORT}",
                              "public_addr": [f"127.0.0.1:{PROXY_PORT}"]}}
    return yaml.safe_dump(conf, sort_keys=False)


def node_id(name: str) -> str:
    """A stable UUID per node name: the resource name Teleport wants for an OpenSSH node, and part of its host
    certificate, so it must not change between runs."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{name}.{CLUSTER}.workbench"))


def targets(ips: dict) -> dict[str, str]:
    """node name -> address of everything a user can reach through Teleport: the login node and every node."""
    return {"login": ips["login"], **ips["nodes"]}


def resources(sim: dict, ips: dict) -> str:
    """The role, the users and the nodes as one multi-document YAML for `tctl create -f`."""
    t = settings(sim)
    users = [u["name"] for u in sim.get("users", [])]
    docs = [{"kind": "role", "version": "v7", "metadata": {"name": ROLE},
             "spec": {"options": {"max_session_ttl": t["cert_ttl"]},
                      "allow": {"logins": users, "node_labels": {"*": "*"}}}}]
    docs += [{"kind": "user", "version": "v2", "metadata": {"name": u}, "spec": {"roles": [ROLE]}} for u in users]
    for name, ip in targets(ips).items():
        kind = "login" if name == "login" else "gpu" if name.startswith("gpu") else "compute"
        docs.append({"kind": "node", "version": "v2", "sub_kind": "openssh",
                     "metadata": {"name": node_id(name), "labels": {"kind": kind}},
                     "spec": {"addr": f"{ip}:22", "hostname": name}})
    return yaml.safe_dump_all(docs, sort_keys=False)


def prepare(sim: dict, ips: dict, conf: Path = CONF_DIR, secrets: Path = SECRETS_DIR) -> None:
    """Write the config and resource files and make the directories the container writes to (owned by this user:
    Docker would create them as root, and the container runs as the invoking user)."""
    data = conf / "data"
    fresh = not data.is_dir() or not any(data.iterdir())  # a new cluster has a new CA: what the old one signed is useless
    data.mkdir(parents=True, exist_ok=True)
    (secrets / "hosts").mkdir(parents=True, exist_ok=True, mode=0o700)
    if fresh:
        for old in (*(secrets / "hosts").iterdir(), secrets / "openssh_ca.pub"):
            old.unlink(missing_ok=True)
    (secrets / "identity").mkdir(exist_ok=True, mode=0o700)
    (conf / "teleport.yaml").write_text(config(sim))
    (conf / "resources.yaml").write_text(resources(sim, ips))


def _tctl(runner: Callable, dc: Callable, *args: str):
    return runner(dc("exec", "-T", "teleport", "tctl", *args))


def wait_ready(runner: Callable, dc: Callable, timeout: float = 90, sleep: Callable[[float], None] = time.sleep) -> bool:
    end = time.monotonic() + timeout
    while True:
        if _tctl(runner, dc, "status").returncode == 0:
            return True
        if time.monotonic() >= end:
            return False
        sleep(2)


def fetch_ca(port: int = PROXY_PORT, opener: Callable | None = None) -> str:
    """The OpenSSH user CA public key from the proxy (Teleport's documented source for `TrustedUserCAKeys`)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # the proxy's certificate is self-signed
    opener = opener or (lambda url: urllib.request.urlopen(url, context=ctx, timeout=10).read().decode())
    text = opener(f"https://127.0.0.1:{port}/webapi/auth/export?type=openssh").strip()
    if not text.startswith("cert-authority"):
        raise TeleportError("the proxy did not return an OpenSSH CA key")
    return text.removeprefix("cert-authority ").strip() + "\n"


def bootstrap(sim: dict, ips: dict, runner: Callable, dc: Callable, say: Callable[[str], None] = print,
              secrets: Path = SECRETS_DIR, fetch: Callable[[], str] | None = None,
              sleep: Callable[[float], None] = time.sleep) -> bool:
    """Load the role, users and nodes, install the CA key, sign a host certificate for every node that has none,
    and write a fresh identity file per user. Safe to repeat. Returns False after saying why it failed."""
    if not wait_ready(runner, dc, sleep=sleep):
        say("FAILED teleport: the auth service did not become ready (docker logs workbench-teleport-1)")
        return False
    r = _tctl(runner, dc, "create", "-f", "/etc/teleport/resources.yaml")
    if r.returncode != 0:
        say(f"FAILED teleport resources: {r.stderr.strip()[-300:]}")
        return False
    try:
        (secrets / "openssh_ca.pub").write_text((fetch or fetch_ca)())
    except (OSError, TeleportError) as e:
        say(f"FAILED teleport CA key: {e}")
        return False
    for name, ip in targets(ips).items():
        if (secrets / "hosts" / f"{name}-cert.pub").is_file():
            continue
        r = _tctl(runner, dc, "auth", "sign", f"--host={ip},{name},{node_id(name)}", "--format=openssh",
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
