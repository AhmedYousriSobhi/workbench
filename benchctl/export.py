"""`benchctl export profile`: the workbench as a profile file a monitoring client can import (issue 91).

The client's file format identifier names the client, so it is never written in this repository: it is read from
`--format`, `$BENCH_EXPORT_FORMAT` or `.git/export-format` (one line, `ID` or `ID:VERSION`, never committed).
The file holds settings only: no password, token or key path (the client leaves those out too)."""
import json
import os
from pathlib import Path

from . import compose, nodes, stack, workbench

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


def build(sim: dict, services: set[str], name: str = "workbench", include_bmc: bool = False,
          user: str | None = None, live: bool = False) -> dict:
    """The profile for what the workbench contains. `services` are the compose service names that exist, so a part
    that is not built (no Jira, no Grafana) is left out instead of pointing at nothing. With `live`, `services` are
    the running containers, so a BMC target is listed only for a node that is up."""
    users = [u["name"] for u in sim.get("users", [])]
    ssh_user = user or (users[0] if users else "alice")
    if user and users and user not in users:
        raise ExportError(f"unknown user {user!r}: sim.yaml has {', '.join(users)}")
    if "login" not in services:
        raise ExportError("this profile has no login node (group 'access'): a client cannot connect by SSH")
    cluster: dict = {
        "name": name,
        "description": "Simulated cluster (workbench). Synthetic data on 127.0.0.1.",
        "tags": ["simulated"],
        "connection": {"host": "127.0.0.1", "port": 2222, "username": ssh_user, "authMethod": "private-key"},
        "grafana": None, "jira": None, "azureTunnel": None, "teleport": None, "scheduler": None, "k8s": None,
        "storage": {"paths": ["~"], "autoRefresh": False},
    }
    if "slurmctld" in services:
        cluster["scheduler"] = {"kind": "slurm", "scope": "mine", "partitions": [p["name"] for p in sim.get("partitions", [])],
                                "intervalSec": 60, "autoRefresh": True}
    if "grafana" in services:
        cluster["grafana"] = {"baseUrl": "http://127.0.0.1:3000", "dashboardUids": ["hpc-overview"],
                              "gpuDatasourceUid": "prometheus", "gpuHostLabel": "Hostname"}
    if "jira-mock" in services:
        cluster["jira"] = {"baseUrl": "http://127.0.0.1:8080", "authMode": "datacenter", "projectKey": "OPS",
                           "wholeProject": True}
    out: dict = {"profile": {"name": name}, "clusters": [cluster]}
    if include_bmc and "bmc-farm" in services:
        out["bmc"] = [{"name": f"bmc-{n}", "address": "127.0.0.1", "port": BMC_PORT, "node": n, "username": "admin",
                       "cluster": 0} for n, _ in nodes.node_names(sim) if not live or n in services]
    return out


def warnings(services: set[str]) -> list[str]:
    """One line per condition the client will trip over; the file is still written."""
    if "slurmctld" in services and "slurmdbd" not in services:
        return [("Slurm accounting is not part of this cluster, so `sacct` says accounting storage is disabled and the "
                 "client cannot read job history: rebuild with `benchctl up --with accounting`")]
    return []


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
    return sim, services_for(sim, prof), prof


def running(base: dict, runner=stack._run, saved=None) -> tuple[dict, set[str]] | None:
    """(sim, services) from the containers running now, or None when none run. Node counts come from the selection
    on disk, but only what Docker reports as running is listed."""
    services = set(stack._project_services(runner))
    if not services:
        return None
    spec = workbench.current(base, saved or workbench.SAVED)
    return (workbench.effective(base, spec)[0] if spec else base), services
