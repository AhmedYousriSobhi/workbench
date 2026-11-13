"""The read side of the service and authentication faults (`get faults`, ADR 0062).

Node faults come from the page's node cards (`webui/cluster.py` classify). The faults a scenario plays on a service or on a
credential (`benchctl/authfaults.py`, `POST /sim/failure` on the Jira mock) have no node card, so each is read back here:
one small question per fault, answered from the component itself or from the file the fault rewrites. A component that does
not answer (down, or an image that predates the read call) is skipped, never reported as a fault, so "none" means none seen.
Nothing here returns a secret: a token's expiry time is read from its payload and the value is never kept.
"""
from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from pathlib import Path

from . import authfaults

BMC_TLS = "http://127.0.0.1:8000/sim/tls"
JIRA_FAILURE = "http://127.0.0.1:8080/sim/failure"


def row(target: str, kind: str, label: str) -> dict:
    return {"target": target, "kind": kind, "state": "fault", "label": label}


def _get(fetch: Callable, url: str):
    try:
        got = fetch(url, None, 0.5)
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def jira_failure(fetch: Callable) -> list[dict]:
    """A failure mode set by `POST /sim/failure` that still has requests to fail."""
    f = _get(fetch, JIRA_FAILURE)
    if not f or not f.get("mode") or int(f.get("count") or 0) <= 0:
        return []
    return [row("jira-mock", "service", f"Failure mode {f['mode']} for the next {f['count']} request(s)")]


def bmc_tls(fetch: Callable) -> list[dict]:
    t = _get(fetch, BMC_TLS)
    return [row("bmc-farm", "auth", "TLS certificate expired")] if t and t.get("expired") else []


def k8s_cert(pki: Path = authfaults.K8S_PKI) -> list[dict]:
    """The expiry step keeps the good certificate beside the serving one; when they differ, the dated one is being served."""
    crt, good = pki / "apiserver.crt", pki / "apiserver.crt.good"
    try:
        expired = good.is_file() and crt.is_file() and crt.read_bytes() != good.read_bytes()
    except OSError:
        return []
    return [row("k8s-api", "auth", "serving certificate expired")] if expired else []


def slurm_token(path: Path = authfaults.TOKEN_FILE, now: float | None = None) -> list[dict]:
    """The saved Slurm REST token, when its own `exp` has passed (slurmrestd then answers 401)."""
    try:
        payload = path.read_text().strip().split(".")[1]
        exp = float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("exp"))
    except (OSError, IndexError, ValueError, TypeError):
        return []
    return [row("slurmrestd", "auth", "saved Slurm token expired")] if exp < (time.time() if now is None else now) else []


def munge(nodes: list[str], runner: Callable) -> list[dict]:
    """A node holding a wrong munge key keeps its own key as munge.key.good (authfaults.MUNGE_WRONG)."""
    out = []
    for n in nodes:
        r = runner(["docker", "exec", f"workbench-{n}-1", "test", "-f", f"{authfaults.MUNGE_KEY}.good"])
        if r.returncode == 0:
            out.append(row(n, "auth", "wrong munge key: slurmctld rejects this node's messages"))
    return out


def collect(fetch: Callable, runner: Callable, running_nodes: list[str]) -> list[dict]:
    return [*jira_failure(fetch), *bmc_tls(fetch), *k8s_cert(), *slurm_token(), *munge(running_nodes, runner)]
