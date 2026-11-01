"""`benchctl export profile`: the workbench as a profile file a monitoring client can import (issue 91).

The client's file format identifier names the client, so it is never written in this repository: it is read from
`--format`, `$BENCH_EXPORT_FORMAT` or `.git/export-format` (one line, `ID` or `ID:VERSION`, never committed).
The file holds settings only: no password, token or key path (the client leaves those out too)."""
import json
import os
from pathlib import Path

from . import addressing, compose, logins, nodes, stack, teleport, workbench

LOCAL_FORMAT_FILE = Path(".git/export-format")
BMC_PORT = 8443


class ExportError(Exception):
    pass


def format_id(flag: str | None = None, env: dict | None = None, local: Path = LOCAL_FORMAT_FILE) -> tuple[str, int]:
    """(identifier, version) from the flag, then the environment, then the local file."""
    raw = flag or (env if env is not None else os.environ).get("BENCH_EXPORT_FORMAT") or (
        local.read_text().strip() if local.is_file() else "")
    if not raw:
        raise ExportError("the profile format identifier is not set. It names the client, so it is kept out of the "
                          "repository: put it on one line in .git/export-format (ID or ID:VERSION), or pass --format "
                          "ID[:VERSION], or set BENCH_EXPORT_FORMAT. See docs/guides/export-profile.md")
    ident, _, version = raw.partition(":")
    if not ident or (version and not version.isdigit()):
        raise ExportError(f"bad format {raw!r}: expected ID or ID:VERSION (VERSION a number)")
    return ident, int(version or 1)


def login_routes(sim: dict, plan: dict, index: int, login: str, mode: str, user: str, loopback: bool) -> list[tuple[str, dict, dict | None]]:
    """The ways into one login node, as (route name, connection, teleport block): `ssh` and `both` connect by key, `teleport` and `both`
    go through the proxy. A client describes a Teleport route as the node's name plus a `teleport` block (proxy, user, insecure) and
    signs in itself with `tsh login` (password and one-time code, `benchctl add invite`): no identity file, no key and no port is in the file."""
    routes = []
    if mode in logins.DIRECT:
        host, port = ("127.0.0.1", compose.SSH_PORT + index) if loopback else (addressing.address_of(plan, login), 22)
        routes.append(("direct", {"host": host, "port": port, "username": user, "authMethod": "private-key"}, None))
    if mode in logins.VIA_TELEPORT:
        proxy = f"127.0.0.1:{teleport.PROXY_PORT}" if loopback else teleport.proxy_addr(sim)
        routes.append(("teleport", {"host": login, "port": 22, "username": user, "authMethod": "agent"},
                       {"proxy": proxy, "user": user, "insecure": True}))  # self-signed proxy; the node name is the one Teleport registered
    return routes


def login_cluster(name: str, login: str, route: str, mode: str, connection: dict, tele: dict | None) -> dict:
    return {
        "name": name,
        "description": f"Simulated cluster (workbench), login node {login}, {route}"
                       f"{' (this node accepts both)' if mode == 'both' else ''}. Synthetic data on 127.0.0.1.",
        "tags": ["simulated", f"login:{login}"],
        "connection": connection,
        "grafana": None, "jira": None, "azureTunnel": None, "scheduler": None, "k8s": None, "teleport": tele,
        "storage": {"paths": ["~"], "autoRefresh": False},
    }


def build(sim: dict, services: set[str], name: str = "workbench", include_bmc: bool = False,
          user: str | None = None, live: bool = False, bmc_via: str = "ip", loopback: bool = False) -> dict:
    """The profile for what the workbench contains. `services` are the compose service names that exist, so a part
    that is not built (no Jira, no Grafana) is left out instead of pointing at nothing. With `live`, `services` are
    the running containers, so a BMC target is listed only for a node that is up. Addresses are the containers' fixed
    private IPs at their standard ports (ADR 0018); `loopback` writes the published 127.0.0.1 ports instead, for a
    client that cannot route to the Docker network."""
    plan = addressing.plan(sim)
    ips = plan["services"]
    web = lambda svc, port: f"http://127.0.0.1:{port}" if loopback else f"http://{ips[svc]}:{port}"
    users = [u["name"] for u in sim.get("users", [])]
    ssh_user = user or (users[0] if users else "alice")
    if user and users and user not in users:
        raise ExportError(f"unknown user {user!r}: sim.yaml has {', '.join(users)}")
    present = [(i, n, m) for i, (n, m) in enumerate(logins.nodes(sim)) if n in services]  # a login that is not built is left out
    if "login" not in services or not present:
        raise ExportError("this profile has no login node (group 'access'): a client cannot connect by SSH")
    routes = [(n, m, r, c, t) for i, n, m in present for r, c, t in login_routes(sim, plan, i, n, m, ssh_user, loopback)]
    routes.sort(key=lambda x: x[2] == "teleport")  # the first entry (it carries Slurm, Grafana, Jira) is a key login, if there is one
    names = [name if k == 0 else f"{name}-{n}" + ("-teleport" if r == "teleport" and m == "both" else "") for k, (n, m, r, c, t) in enumerate(routes)]
    clusters = [login_cluster(nm, n, r, m, c, t) for nm, (n, m, r, c, t) in zip(names, routes, strict=True)]
    cluster = clusters[0]
    if "slurmctld" in services:
        cluster["scheduler"] = {"kind": "slurm", "scope": "mine", "partitions": [p["name"] for p in sim.get("partitions", [])],
                                "intervalSec": 60, "autoRefresh": True}
    if "grafana" in services:
        cluster["grafana"] = {"baseUrl": web("grafana", 3000), "dashboardUids": ["hpc-overview"],
                              "gpuDatasourceUid": "prometheus", "gpuHostLabel": "Hostname"}
    if "jira-mock" in services:
        cluster["jira"] = {"baseUrl": web("jira-mock", 8080), "authMode": "datacenter", "projectKey": "OPS",
                           "wholeProject": True}
    out: dict = {"profile": {"name": name}, "clusters": clusters}
    if include_bmc and "bmc-farm" in services:
        if bmc_via not in ("ip", "name"):
            raise ExportError(f"bad --bmc-via {bmc_via!r}: ip or name")
        every = [n for n, _ in nodes.node_names(sim)]
        if loopback:  # the one published listener; the node is chosen by the Host header, so every target looks alike
            targets = [(n, "127.0.0.1", BMC_PORT) for n in every]
        else:  # each BMC has its own address on the ipmi network, reachable from here and from the login node (ADR 0018)
            bmc_ip = addressing.plan(sim)["bmc"]
            targets = [(n, bmc_ip[n] if bmc_via == "ip" else f"bmc-{n}", 443) for n in every if f"bmc-{n}" in services]
        out["bmc"] = [{"name": f"bmc-{n}", "address": a, "port": p, "node": n, "username": "admin", "cluster": 0}
                      for n, a, p in targets]
    return out


def warnings(services: set[str], sim: dict | None = None) -> list[str]:
    """One line per condition the client will trip over; the file is still written."""
    out = []
    if "slurmctld" in services and "slurmdbd" not in services:
        out.append("Slurm accounting is not part of this cluster, so `sacct` says accounting storage is disabled and the "
                   "client cannot read job history: rebuild with `benchctl up --with accounting`")
    if sim and any(m in logins.VIA_TELEPORT for n, m in logins.nodes(sim) if n in services):
        out.append("a login node is reached through Teleport: sign in from the client (its Log in button, password and one-time code from "
                   "`benchctl add invite <user>`); the client does not use the identity files, and the file holds no credential")
    return out


def to_json(body: dict, fmt: tuple[str, int]) -> str:
    """Wrap the profile in the client's envelope (`format`, `version`) and serialise it."""
    return json.dumps({"format": fmt[0], "version": fmt[1], **body}, indent=2) + "\n"


def services_for(sim: dict, profile: str) -> set[str]:
    """Service names the profile generates (what `up` would start), from sim.yaml and the profile alone."""
    return {*compose.generate(sim, compose.load_profile(profile))["services"], "grafana"}  # observability is always on


def configured(base: dict, profile: str | None = None, saved=None) -> tuple[dict, set[str], str]:
    """(sim, services, label) from the selection on disk: the workbench last built, else the profile `up` started.
    Needs no Docker, so the web page (which has none, SPEC W-3) uses it."""
    spec = workbench.current(base, saved or workbench.SAVED)
    sim = workbench.effective(base, spec)[0] if spec else base
    if spec and not profile:
        return sim, set(compose.generate(*workbench.effective(base, spec))["services"]) | {"grafana"}, "workbench"
    prof = profile or stack.current_profile()
    try:
        return sim, services_for(sim, prof), prof
    except FileNotFoundError as e:
        raise ExportError(f"no profile {prof!r} and no saved workbench to describe: start the cluster, or pass "
                          "--profile min|std|full") from e


def running(base: dict, runner=stack._run, saved=None) -> tuple[dict, set[str]] | None:
    """(sim, services) from the containers running now, or None when none run. Node counts come from the selection
    on disk, but only what Docker reports as running is listed."""
    services = set(stack._project_services(runner))
    if not services:
        return None
    spec = workbench.current(base, saved or workbench.SAVED)
    return (workbench.effective(base, spec)[0] if spec else base), services
