"""Real expiry and authentication faults for scenarios (issue 549, config lab A-15): the `auth` target.

Each step makes the real component answer with its real error; nothing is simulated in the client's path:

  slurm_token_ttl: {user, ttl}   issue a Slurm JWT that lives `ttl` and save it to compose/generated/secrets/slurm_token; slurmrestd answers 401 after
  teleport_cert_ttl: {user, ttl} re-sign the user's identity file with a short life; `tsh`/ssh then say the certificate has expired
  teleport_cert_restore: [users] sign the identity again with the cluster's normal life (sim.yaml teleport.cert_ttl)
  munge_wrong_key: [nodes]       a node gets another munge key: slurmctld logs "Invalid credential"; the original is kept inside the node
  munge_restore: [nodes]         the node's own original key goes back (never the image's: keys generated at `up`, issue 465, stay safe)
  bmc_tls_expired: true|false    the BMC farm presents a certificate that ended yesterday (same key and name), or the good one again
  k8s_cert_expired: true|false   the API server restarts with a serving certificate that ended yesterday, or with the good one again

Every step runs host commands or posts to the farm, so the target needs the host CLI (the page cannot play it).
"""
from __future__ import annotations

import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

KEYS = ("slurm_token_ttl", "teleport_cert_ttl", "teleport_cert_restore", "munge_wrong_key", "munge_restore", "bmc_tls_expired", "k8s_cert_expired")
SLURMCTLD = "workbench-slurmctld-1"
APISERVER = "workbench-k8s-apiserver-1"
BMC_TLS_URL = "http://127.0.0.1:8000/sim/tls"
TOKEN_FILE = Path("compose/generated/secrets/slurm_token")
K8S_PKI = Path("compose/generated/secrets/k8s")
_TTL = re.compile(r"(\d+)([smh])")
MUNGE_KEY = "/etc/munge/munge.key"
# Keep the node's own key once (cp -n), write another one, restart munged as the munge user. A second wrong_key keeps the first backup.
MUNGE_WRONG = (f"cp -n {MUNGE_KEY} {MUNGE_KEY}.good && dd if=/dev/urandom bs=1 count=1024 of={MUNGE_KEY} 2>/dev/null && chown munge:munge {MUNGE_KEY} && "
               f"chmod 0400 {MUNGE_KEY} && pkill munged; su -s /bin/sh munge -c 'munged --force'")
MUNGE_RIGHT = (f"test -f {MUNGE_KEY}.good && cp {MUNGE_KEY}.good {MUNGE_KEY} && chown munge:munge {MUNGE_KEY} && chmod 0400 {MUNGE_KEY} && "
               f"rm {MUNGE_KEY}.good && pkill munged; su -s /bin/sh munge -c 'munged --force'")


def seconds(text) -> int | None:
    m = _TTL.fullmatch(str(text).strip())
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)] if m else None


def problems(do: dict, nodes: set[str], users: set[str]) -> list[str]:
    out = [f"unknown auth key {k!r} (one of {', '.join(KEYS)})" for k in do if k not in KEYS]
    if not any(k in do for k in KEYS):
        return out or [f"auth needs one of: {', '.join(KEYS)}"]
    for key in ("slurm_token_ttl", "teleport_cert_ttl"):
        if key in do:
            spec = do[key]
            if not isinstance(spec, dict) or spec.get("user") not in users or seconds(spec.get("ttl", "")) in (None, 0):
                out.append(f"{key} needs a mapping: user (one of the simulated users) and ttl like 30s, 5m or 1h")
    for key in ("teleport_cert_restore", "munge_wrong_key", "munge_restore"):
        if key in do:
            names, known = do[key], (users if key == "teleport_cert_restore" else nodes)
            if not isinstance(names, list) or not names:
                out.append(f"{key} needs a list of {'users' if known is users else 'nodes'}")
                continue
            out += [f"{key}: unknown {'user' if known is users else 'node'} {n!r}" for n in names if n not in known]
    for key in ("bmc_tls_expired", "k8s_cert_expired"):
        if key in do and not isinstance(do[key], bool):
            out.append(f"{key} is true (expire) or false (restore)")
    return out


def _fail(r) -> str:
    return (r.stderr or r.stdout or f"exit {r.returncode}").strip()[-300:]


def apply(do: dict, runner: Callable, poster: Callable, sim: dict, *, now: float | None = None) -> str:
    """Run the step's faults in a fixed order. Returns "" when all worked, else the first failure."""
    from . import (  # late: they are only needed (and importable without cycles) at run time
        stack,
        teleport,
    )
    if "slurm_token_ttl" in do:
        spec = do["slurm_token_ttl"]
        r = runner(["docker", "exec", SLURMCTLD, "scontrol", "token", f"username={spec['user']}", f"lifespan={seconds(spec['ttl'])}"])
        token = re.search(r"SLURM_JWT=(\S+)", r.stdout or "")
        if r.returncode or not token:
            return f"slurm token: {_fail(r)}"
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        TOKEN_FILE.write_text(token.group(1) + "\n")
        TOKEN_FILE.chmod(0o600)
    if "teleport_cert_ttl" in do:
        spec = do["teleport_cert_ttl"]
        bad = _sign(runner, stack.dc, spec["user"], str(spec["ttl"]))
        if bad:
            return bad
    for user in do.get("teleport_cert_restore") or []:
        bad = _sign(runner, stack.dc, user, teleport.settings(sim)["cert_ttl"])
        if bad:
            return bad
    for key, script in (("munge_wrong_key", MUNGE_WRONG), ("munge_restore", MUNGE_RIGHT)):
        for n in do.get(key) or []:
            r = runner(["docker", "exec", "-u", "root", f"workbench-{n}-1", "sh", "-c", script])
            if r.returncode:
                return f"munge on {n}: {_fail(r)}"
    if "bmc_tls_expired" in do:
        poster(BMC_TLS_URL, {"expired": do["bmc_tls_expired"]})
    if "k8s_cert_expired" in do:
        return k8s_cert(do["k8s_cert_expired"], runner, K8S_PKI, now)
    return ""


def _sign(runner: Callable, dc: Callable, user: str, ttl: str) -> str:
    from . import teleport
    r = runner(dc("exec", "-T", "teleport", "tctl", "auth", "sign", f"--user={user}", f"--ttl={ttl}", "--format=file",
                  f"--out={teleport.CONTAINER_SECRETS}/identity/{user}", "--overwrite"))
    return f"teleport certificate for {user}: {_fail(r)}" if r.returncode else ""


def _stamp(t: float) -> str:
    return time.strftime("%y%m%d%H%M%SZ", time.gmtime(t))


def k8s_cert(expired: bool, runner: Callable, pki: Path, now: float | None = None) -> str:
    """Swap the API server's serving certificate for one signed by the same CA that ended yesterday (or back), then restart the API server.
    The good certificate is kept beside it as apiserver.crt.good; `openssl ca` makes the dated one (openssl x509 only learnt dates in 3.4)."""
    crt, good = pki / "apiserver.crt", pki / "apiserver.crt.good"
    if not expired:
        if not good.is_file():
            return ""  # never expired: nothing to put back
        good.replace(crt)
    else:
        if not good.is_file():
            good.write_bytes(crt.read_bytes())
        san = runner(["openssl", "x509", "-in", str(good), "-noout", "-ext", "subjectAltName"])
        if san.returncode:
            return f"openssl: {_fail(san)}"
        names = ",".join(x.strip().replace("IP Address:", "IP:") for x in san.stdout.splitlines()[1:] if x.strip())
        t = time.time() if now is None else now
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "index.txt").write_text("")
            (d / "serial").write_text("01\n")
            (d / "ca.cnf").write_text(f"[ca]\ndefault_ca=c\n[c]\ndatabase={d}/index.txt\nserial={d}/serial\nnew_certs_dir={d}\ndefault_md=sha256\n"
                                      "policy=p\nunique_subject=no\n[p]\ncommonName=supplied\n")
            (d / "ext").write_text("basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nauthorityKeyIdentifier=keyid\n"
                                   f"extendedKeyUsage=serverAuth,clientAuth\nsubjectAltName={names}\n")
            for cmd in (["openssl", "req", "-new", "-key", str(pki / "apiserver.key"), "-subj", "/CN=kube-apiserver", "-out", str(d / "a.csr")],
                        ["openssl", "ca", "-batch", "-notext", "-config", str(d / "ca.cnf"), "-cert", str(pki / "ca.crt"), "-keyfile", str(pki / "ca.key"),
                         "-startdate", _stamp(t - 40 * 86400), "-enddate", _stamp(t - 86400), "-extfile", str(d / "ext"),
                         "-in", str(d / "a.csr"), "-out", str(d / "a.crt")]):
                r = runner(cmd)
                if r.returncode:
                    return f"openssl {cmd[1]}: {_fail(r)}"
            crt.write_bytes((d / "a.crt").read_bytes())
    r = runner(["docker", "restart", "-t", "5", APISERVER])
    return f"restart API server: {_fail(r)}" if r.returncode else ""
