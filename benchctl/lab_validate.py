"""Config lab validation with the real tools (issue 552, ADR 0081, SPEC C-24): `benchctl validate lab NAME`.

Checks the lab's *baseline* (the imported production configuration, rendered for the lab) with throwaway one-shots:

    slurm.conf         a throwaway slurmctld on the rendered config (its own internal network, empty state, no live key)
    sacctmgr load      the dump and QOS loaded into a scratch database through a throwaway slurmdbd, then diffed
    promtool           `check rules` and `test rules`
    kubectl            `apply --dry-run=server` against the lab's API server
    teleport role      structure check; plus `tctl create` of a temporary role when the lab's Teleport runs
    secret scan, versions

Every result is `{tool, command, ok, messages[], restart_required}` (ok is None when skipped) plus `status` and `note`.
All commands go through one injectable executor `ex(argv, stdin=None, timeout=60) -> (returncode, output)`, so unit tests
never start a container. One-shots carry no `workbench.class` label (the governor would freeze them) and are removed after.
`--side candidate` waits for issue 553 (the candidate overlay).
"""
from __future__ import annotations

import contextlib
import difflib
import json
import re
import secrets
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import yaml

from . import lab as lab_store
from . import lab_import as li
from . import lab_slurm
from .lab_import import ImportError_ as LabError

Executor = Callable[..., tuple[int, str]]

SLURM_IMAGE = "workbench/slurm-ctl:dev"
PROM_IMAGE = "prom/prometheus:v2.53.0"  # the pin of compose/observability.yml (a test keeps them in step)
PROJECT = "workbench"
MARIADB, TELEPORT, K8S_API = f"{PROJECT}-mariadb-1", f"{PROJECT}-teleport-1", f"{PROJECT}-k8s-apiserver-1"
NETWORK = f"{PROJECT}_default"
DB_PREFIX, DBD_PORT = "lab_validate_", 6899
START_WAIT_S = 20  # slurmctld must report that it started within this
ONE_SHOT_TIMEOUT_S = 90
SIDES = ("baseline", "candidate")
NOT_AVAILABLE = 3  # exit code of `--side candidate` until issue 553: not a failure, not a pass
# slurmctld logs "started on cluster" BEFORE it checks the nodes, partitions and plugins (a bad partition QOS is a `fatal:` 5 ms later),
# so only the line it writes when the whole configuration is loaded counts (captured from 25.11 and 26.05, issue 778)
STARTED = re.compile(r"Running as primary controller", re.IGNORECASE)
SAFE = re.compile(r"[A-Za-z0-9_.@:=,/<>+ -]*")
IDENT = re.compile(r"[A-Za-z0-9_.-]+")


def run_cmd(argv: list[str], stdin: str | None = None, timeout: int = 60) -> tuple[int, str]:
    """The real executor: (returncode, stdout + stderr). A timeout is returncode 124."""
    try:
        r = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out + f"\ntimed out after {timeout} s"
    except FileNotFoundError:
        return 127, f"{argv[0]}: command not found"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def result(tool: str, command: str, status: str, messages: list[str] | None = None, restart_required: bool = False,
           note: str | None = None, warnings: list[str] | None = None) -> dict:
    """status: passed | failed | skipped. `ok` is True, False or None (skipped)."""
    return {"tool": tool, "command": command, "ok": {"passed": True, "failed": False}.get(status), "status": status,
            "messages": messages or [], "warnings": warnings or [], "restart_required": restart_required, "note": note}


def _running(ex: Executor, container: str) -> bool:
    rc, out = ex(["docker", "inspect", "-f", "{{.State.Running}}", container], timeout=15)
    return rc == 0 and out.strip() == "true"


def _first_line(out: str) -> str:
    return next((ln.strip() for ln in out.splitlines() if ln.strip()), "no output")


# ---------------------------------------------------------------- render (the lab's own configuration)

def _members(spec: str, nodes: set[str], sets: dict[str, str]) -> list[str]:
    if spec.upper() == "ALL":
        return sorted(nodes)
    out: list[str] = []
    for part in lab_slurm._split_commas(spec):
        out += _members(sets[part], nodes, sets) if part in sets else li.expand_hostlist(part)
    return out


def render(name: str, sim: dict, root: Path | None = None, factor: float | None = None) -> dict:
    """Write compose/generated/lab/NAME/render/: the baseline as the lab would run it (downscaled nodes, lab-owned keys from the
    workbench, time-scaled keys divided by the dilation factor). Returns {dir, kept_nodes, replaced, applied, restart_keys}."""
    from . import slurmconf

    b = lab_store.load_baseline(name, root)
    if b["slurm"] is None:
        raise LabError(f"lab {name} has no slurm.conf: nothing to validate for Slurm")
    sl = b["slurm"]
    plan = lab_store.plan(name, sim, root)
    kept = set(plan["scale"]["nodes"])
    factor = plan["time"]["factor"] if factor is None else factor
    ours = li.parse_slurm_conf(slurmconf.slurm_conf(sim))["keys"]
    keys, _ = lab_slurm.dilate_keys(sl["keys"], factor)
    out_keys = dict(ours)
    replaced, applied, restart = [], [], []
    for k, v in keys.items():
        if lab_slurm.classify(k) == "lab-owned":
            replaced.append(k)
            continue
        out_keys[k] = v
        applied.append(k)
        if lab_slurm.restart_required(k):
            restart.append(k)
    sets = {o["NodeSet"]: o.get("Nodes", "") for o in sl["other"] if "NodeSet" in o}
    all_nodes = {n for line in sl["nodes"] for n in li.expand_hostlist(line["NodeName"])}
    lines = [f"# RENDERED by `benchctl validate lab {name}`; the lab's own copy of the imported configuration"]
    lines += [f"{k}={v}" for k, v in out_keys.items()]
    for line in sl["nodes"]:
        for n in li.expand_hostlist(line["NodeName"]):
            if n in kept:
                attrs = {k: v for k, v in line.items() if k not in ("NodeName", "NodeAddr", "NodeHostname", "State", "Port")}
                lines.append("NodeName=" + n + "".join(f" {k}={v}" for k, v in attrs.items()) + " State=UNKNOWN")
    for p in sl["partitions"]:
        mem = [n for n in _members(p.get("Nodes", ""), all_nodes, sets) if n in kept]
        if mem:
            q = lab_slurm.dilate_partition({k: v for k, v in p.items() if k != "Nodes"}, factor)
            lines.append("PartitionName=" + q.pop("PartitionName") + " Nodes=" + ",".join(mem) + "".join(f" {k}={v}" for k, v in q.items()))
    d = lab_store.path(name, root) / "render"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "slurm.conf").write_text("\n".join(lines) + "\n")
    (d / "cgroup.conf").write_text("CgroupPlugin=disabled\n")
    topo = _render_topology(b["topology"], kept)
    if topo:
        (d / "topology.conf").write_text(topo)
    gres = [r for r in b["gres"] if "NodeName" not in r or kept & set(li.expand_hostlist(r["NodeName"]))]
    if gres:
        (d / "gres.conf").write_text("\n".join(" ".join(f"{k}={v}" for k, v in r.items()) for r in gres) + "\n")
    return {"dir": str(d), "kept_nodes": len(kept), "replaced": sorted(replaced), "applied": sorted(applied), "restart_keys": sorted(restart),
            "host": out_keys.get("SlurmctldHost", "slurmctld")}


def _render_topology(rows: list[dict[str, str]], kept: set[str]) -> str:
    """topology.conf limited to the lab's nodes; a switch with nothing left under it is dropped."""
    alive: dict[str, dict[str, str]] = {}
    for r in rows:
        label = r.get("SwitchName") or r.get("BlockName")
        if "Nodes" in r:
            mem = [n for n in li.expand_hostlist(r["Nodes"]) if n in kept]
            if mem and label:
                alive[label] = {**r, "Nodes": ",".join(mem)}
    for _ in range(len(rows)):  # parents of live switches, repeatedly
        for r in rows:
            label = r.get("SwitchName") or r.get("BlockName")
            kids = [s for s in lab_slurm._split_commas(r.get("Switches", "")) if s in alive]
            if kids and label:
                alive[label] = {**r, "Switches": ",".join(kids)}
    return "".join(" ".join(f"{k}={v}" for k, v in r.items()) + "\n" for r in alive.values())


# ---------------------------------------------------------------- slurm.conf

SLURM_SCRIPT = f"""set -u
export PATH=/opt/slurm/sbin:/opt/slurm/bin:$PATH
mkdir -p /etc/slurm /etc/munge /run/munge /var/log/munge /var/lib/munge /var/spool/slurmctld /var/spool/slurmd
cp /render/* /etc/slurm/
sed -i '/^PartitionName=/s/ QOS=[^ ]*//' /etc/slurm/slurm.conf  # a partition QOS is checked against accounting, which this one-shot has not
dd if=/dev/urandom of=/etc/munge/munge.key bs=1 count=1024 2>/dev/null
chown -R munge:munge /etc/munge /var/lib/munge /var/log/munge /run/munge
chmod 400 /etc/munge/munge.key
su -s /bin/sh munge -c 'munged --force' || echo 'warning: munged did not start'
dd if=/dev/urandom of=/var/spool/slurmctld/jwt_hs256.key bs=32 count=1 2>/dev/null
chown -R slurm:slurm /var/spool/slurmctld /var/spool/slurmd
chmod 0600 /var/spool/slurmctld/jwt_hs256.key
slurmctld -D -vvv >/tmp/ctld.log 2>&1 &
pid=$!
i=0
while [ $i -lt {START_WAIT_S * 2} ]; do
  grep -q 'fatal:' /tmp/ctld.log && break
  grep -q 'Running as primary controller' /tmp/ctld.log && break
  kill -0 $pid 2>/dev/null || break
  sleep 0.5
  i=$((i+1))
done
kill $pid 2>/dev/null
sleep 1
cat /tmp/ctld.log
"""


def parse_slurmctld(out: str) -> tuple[bool, list[str], list[str]]:
    """(started, [first fatal line], warnings): a `fatal:` line fails and is shown; `error:` lines are warnings only (unresolvable
    node names, cgroup notices and an unreachable slurmdbd are expected in a container with no network)."""
    fatal = [ln.strip() for ln in out.splitlines() if "fatal:" in ln][:1]
    warn = list(dict.fromkeys(ln.strip() for ln in out.splitlines() if re.search(r"\berror:", ln)))[:10]
    return (bool(STARTED.search(out)) and not fatal), fatal, warn


@contextlib.contextmanager
def throwaway_network(ex: Executor) -> Iterator[str]:
    """A per-run Docker network with `--internal` (no gateway, no route out) that no workbench container is attached to; it is removed
    when the block ends, on an error too. Never the compose network (issue 778, ADR 0081 addendum)."""
    net = f"{PROJECT}-lab-validate-{secrets.token_hex(4)}"
    assert net != NETWORK
    rc, out = ex(["docker", "network", "create", "--internal", net], timeout=30)
    if rc != 0:
        raise LabError(f"could not create the throwaway network: {_first_line(out)}")
    try:
        yield net
    finally:
        ex(["docker", "network", "rm", net], timeout=30)


def check_slurm(render_dir: Path, host: str, restart_keys: list[str], ex: Executor, image: str = SLURM_IMAGE) -> dict:
    """slurmctld needs a non-loopback address to bind (with `--network none` it dies in `_open_ports`, also with SlurmctldHost=name(127.0.0.1):
    captured, issue 778), so the one-shot joins a throwaway `--internal` network of its own: unreachable from and to the live stack."""
    shown = "docker run --rm --network <internal, throwaway> --memory 192m " + image + " slurmctld -D -vvv  (rendered slurm.conf)"
    if not IDENT.fullmatch(host):
        return result("slurm.conf", shown, "failed", [f"SlurmctldHost {host!r} is not a plain host name"])
    try:
        with throwaway_network(ex) as net:
            argv = ["docker", "run", "--rm", "--network", net, "--memory", "192m", "--pids-limit", "256", "--hostname", host,
                    "-v", f"{Path(render_dir).resolve()}:/render:ro", "--entrypoint", "sh", image, "-c", SLURM_SCRIPT]
            rc, out = ex(argv, timeout=ONE_SHOT_TIMEOUT_S)
    except LabError as e:
        return result("slurm.conf", shown, "failed", [str(e)])
    started, fatal, warn = parse_slurmctld(out)
    if started:
        msg = "slurmctld started on the rendered configuration"
        if restart_keys:
            msg += f"; applying it restarts slurmctld ({', '.join(restart_keys[:5])}{' ...' if len(restart_keys) > 5 else ''})"
        return result("slurm.conf", shown, "passed", [msg], bool(restart_keys),
                      warnings=["partition QOS options are not checked here (no accounting in the one-shot); `sacctmgr load` covers the QOS"] + warn)
    if fatal:
        return result("slurm.conf", shown, "failed", [fatal[0]], bool(restart_keys), warnings=warn)
    why = _first_line(out) if rc != 0 and not out.strip().startswith("#") and "slurmctld" not in out else f"slurmctld did not report that it started within {START_WAIT_S} s"
    return result("slurm.conf", shown, "failed", [why], bool(restart_keys), warnings=warn)


# ---------------------------------------------------------------- sacctmgr load

QOS_SET = ("Priority", "GraceTime", "MaxWall", "MaxJobsPerUser", "MaxSubmitJobsPerUser", "MaxTRESPerUser", "MaxTRESPU", "MaxTRESPerJob",
           "MaxTRESPerAccount", "GrpTRES", "GrpJobs", "GrpSubmitJobs", "Preempt", "PreemptMode", "PreemptExemptTime", "Flags", "UsageFactor",
           "UsageThreshold", "MinTRES", "MinTRESPerJob")
_QOS_ALIAS = {"MaxTRESPU": "MaxTRESPerUser"}


_HMS = re.compile(r"(?:(\d+)-)?(\d+):(\d\d):(\d\d)")
TRES_NAME = re.compile(r"\b((?:gres|license|bb)/[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)?)")


def _seconds(v: str) -> str:
    """`sacctmgr show qos` prints GraceTime as [D-]HH:MM:SS but `modify qos set GraceTime=` only takes seconds (captured, issue 778)."""
    m = _HMS.fullmatch(v)
    return str(((int(m.group(1) or 0) * 24 + int(m.group(2))) * 60 + int(m.group(3))) * 60 + int(m.group(4))) if m else v


def tres_names(*texts: str) -> list[str]:
    """Every gres/license TRES the lab's QOS or dump mention: slurmdbd rejects a limit on a TRES it was not told to track."""
    return sorted({m for t in texts for m in TRES_NAME.findall(t)})


def qos_script(rows: list[dict[str, str]]) -> str:
    """sacctmgr commands that create every QOS first (users reference them), then set the options the export gives."""
    names = [r["Name"] for r in rows if r.get("Name") and r["Name"] != "normal"]
    for n in names:
        if not IDENT.fullmatch(n):
            raise LabError(f"QOS name {n!r} has characters the lab does not pass to sacctmgr")
    lines = [f"sacctmgr -i add qos {n}" for n in names]
    for r in rows:
        n = r.get("Name")
        if not n:
            continue
        sets = []
        for k in QOS_SET:
            v = (r.get(k) or "").strip()
            if v and not (k == "PreemptMode" and v == "cluster"):
                if not SAFE.fullmatch(v):
                    raise LabError(f"QOS {n} {k}={v!r} has characters the lab does not pass to sacctmgr")
                sets.append(f"{_QOS_ALIAS.get(k, k)}={shlex.quote(_seconds(v) if k == 'GraceTime' else v)}")
        if sets:
            lines.append(f"sacctmgr -i modify qos where name={n} set " + " ".join(sets))
    return "\n".join(lines) + "\n"


def tres_script(cluster: str, tres: list[str]) -> str:
    """slurmdbd learns the gres/license TRES only from a slurmctld that registers with `AccountingStorageTRES=` (there is no `sacctmgr add tres`;
    captured, issue 778): run one for a moment against the scratch database, with one node and its own state, then stop it. It listens only
    inside this one-shot and knows no live node or database."""
    if not tres:
        return ""
    return f"""printf 'ClusterName={cluster}\\nSlurmctldHost=%s\\nAuthType=auth/munge\\nSlurmUser=slurm\\nStateSaveLocation=/var/spool/slurmctld\\nAccountingStorageType=accounting_storage/slurmdbd\\nAccountingStorageHost=localhost\\nAccountingStoragePort={DBD_PORT}\\nAccountingStorageTRES={",".join(tres)}\\nNodeName=lab1 CPUs=1\\nPartitionName=lab Nodes=lab1\\n' "$(hostname)" > /etc/slurm/slurm.conf
chown -R slurm:slurm /var/spool/slurmctld
slurmctld -D >/tmp/ctld.log 2>&1 &
cpid=$!
i=0
while [ $i -lt 40 ]; do
  grep -q 'Running as primary controller' /tmp/ctld.log && break
  kill -0 $cpid 2>/dev/null || break
  sleep 0.5
  i=$((i+1))
done
kill $cpid 2>/dev/null
sleep 1
"""


def accounting_script(db: str, cluster: str, tres: list[str] | None = None) -> str:
    return f"""set -u
export PATH=/opt/slurm/sbin:/opt/slurm/bin:$PATH
mkdir -p /etc/slurm /etc/munge /run/munge /var/log/munge /var/lib/munge /var/spool/slurmctld
dd if=/dev/urandom of=/etc/munge/munge.key bs=1 count=1024 2>/dev/null
chown -R munge:munge /etc/munge /var/lib/munge /var/log/munge /run/munge
chmod 400 /etc/munge/munge.key
su -s /bin/sh munge -c 'munged --force'
printf 'ClusterName=lab\\nSlurmctldHost=localhost\\nAuthType=auth/munge\\nAccountingStorageType=accounting_storage/slurmdbd\\nAccountingStorageHost=localhost\\nAccountingStoragePort={DBD_PORT}\\n' > /etc/slurm/slurm.conf
printf 'AuthType=auth/munge\\nDbdHost=localhost\\nDbdPort={DBD_PORT}\\nSlurmUser=slurm\\nLogFile=/tmp/dbd.log\\nPidFile=/tmp/dbd.pid\\nStorageType=accounting_storage/mysql\\nStorageHost=mariadb\\nStorageUser=slurm\\nStoragePass=%s\\nStorageLoc={db}\\n' "$MARIADB_PASSWORD" > /etc/slurm/slurmdbd.conf
chown slurm:slurm /etc/slurm/slurmdbd.conf
chmod 600 /etc/slurm/slurmdbd.conf
slurmdbd -D >/tmp/dbd.out 2>&1 &
pid=$!
i=0
until sacctmgr -i show cluster >/dev/null 2>&1; do
  i=$((i+1))
  if [ $i -gt 60 ] || ! kill -0 $pid 2>/dev/null; then echo '@@DBD_FAILED'; cat /tmp/dbd.out /tmp/dbd.log 2>/dev/null; exit 0; fi
  sleep 1
done
{tres_script(cluster, tres or [])}echo '@@QOS'
sh /lab/qos.sh 2>&1
echo '@@LOAD'
sacctmgr -i load file=/lab/sacctmgr.dump 2>&1
echo "@@LOAD_RC=$?"
echo '@@AFTER'
sacctmgr -i dump {cluster} file=/tmp/after.dump >/dev/null 2>&1
cat /tmp/after.dump
echo '@@END'
kill $pid 2>/dev/null
"""


def _section(out: str, name: str) -> str:
    m = re.search(rf"^@@{name}\n(.*?)(?=^@@|\Z)", out, re.DOTALL | re.MULTILINE)
    return m.group(1) if m else ""


def diff_dump(before: str, after: str) -> tuple[list[str], list[str]]:
    """(missing, changed): accounts and users of the input that the loaded database lacks, and options whose value differs."""
    try:
        a, b = li.parse_sacctmgr_dump(before), li.parse_sacctmgr_dump(after)
    except LabError as e:
        return [f"the dump read back from the scratch database could not be parsed: {e}"], []
    missing, changed = [], []
    for kind, label in (("accounts", "account"), ("users", "user")):
        got = {x["name"]: x for x in b[kind]}
        for x in a[kind]:
            if x["name"] not in got:
                missing.append(f"{label} {x['name']} was not loaded")
                continue
            for k, v in (x["opts"] or {}).items():
                w = got[x["name"]]["opts"].get(k)
                if w is not None and w != v and not (k == "QOS" and set(w.split(",")) == set(v.split(","))):  # sacctmgr sorts the list
                    changed.append(f"{label} {x['name']}: {k} {v} -> {w}")
    return missing, changed


def check_accounting(lab_dir: Path, ex: Executor, work: Path, sim_secrets: Path = Path("compose/generated/secrets")) -> dict:
    tool, shown = "sacctmgr load", "sacctmgr -i load file=sacctmgr.dump (throwaway slurmdbd on a scratch database)"
    base = Path(lab_dir) / "baseline"
    if not (base / "sacctmgr.dump").is_file():
        return result(tool, shown, "skipped", ["no sacctmgr.dump in this lab"])
    if not _running(ex, MARIADB):
        return result(tool, shown, "skipped", ["skipped: accounting is not running (start it with `benchctl up --with accounting`)"])
    dump = (base / "sacctmgr.dump").read_text()
    cluster = li.parse_sacctmgr_dump(dump)["cluster"] or "lab"
    if not IDENT.fullmatch(cluster):
        return result(tool, shown, "failed", [f"cluster name {cluster!r} is not a plain name"])
    qos_rows = li.parse_qos((base / "qos.txt").read_text()) if (base / "qos.txt").is_file() else []
    try:
        qos = qos_script(qos_rows)
    except LabError as e:
        return result(tool, shown, "failed", [str(e)])

    def sql(stmt: str) -> tuple[int, str]:
        return ex(["docker", "exec", "-i", MARIADB, "sh", "-c", 'exec mariadb -uslurm -p"$MARIADB_PASSWORD" -N'], stdin=stmt, timeout=30)

    rc, out = sql("SHOW DATABASES LIKE 'lab\\_validate\\_%';")  # leftovers of an interrupted run
    for old in [ln.strip() for ln in out.splitlines() if ln.strip().startswith(DB_PREFIX)] if rc == 0 else []:
        if IDENT.fullmatch(old):
            sql(f"DROP DATABASE IF EXISTS `{old}`;")
    run_id = secrets.token_hex(4)
    db, name = f"{DB_PREFIX}{run_id}", f"lab-validate-{run_id}"
    rc, out = sql(f"CREATE DATABASE `{db}`;")
    if rc != 0:
        return result(tool, shown, "failed", [f"could not create the scratch database: {_first_line(out)}"])
    try:
        work.mkdir(parents=True, exist_ok=True)
        (work / "sacctmgr.dump").write_text(dump)
        (work / "qos.sh").write_text(qos)
        rc, out = ex(["docker", "run", "--rm", "--name", name, "--network", NETWORK, "--memory", "128m", "--pids-limit", "256",
                      "--env-file", str((sim_secrets / "mariadb.env").resolve()), "-v", f"{work.resolve()}:/lab:ro",
                      "--entrypoint", "sh", SLURM_IMAGE, "-c", accounting_script(db, cluster, tres_names(dump, *[" ".join(r.values()) for r in qos_rows]))], timeout=ONE_SHOT_TIMEOUT_S)
    finally:
        ex(["docker", "rm", "-f", name], timeout=20)
        sql(f"DROP DATABASE IF EXISTS `{db}`;")
    if "@@DBD_FAILED" in out and re.search(r"routine command denied", out, re.IGNORECASE):
        why = ("skipped: slurmdbd needs CREATE ROUTINE and ALTER ROUTINE on the scratch databases `lab_validate_%`; "
               "the grant `benchctl up` applies does not give them yet (pending owner approval)")
        return result(tool, shown, "skipped", [why])
    if "@@DBD_FAILED" in out:
        return result(tool, shown, "failed", ["the throwaway slurmdbd did not come up: " + _first_line(_section(out, "DBD_FAILED") or out)])
    if "@@LOAD_RC=" not in out:
        return result(tool, shown, "failed", [f"no result from the one-shot: {_first_line(out)}"])
    load_rc = int(re.search(r"@@LOAD_RC=(\d+)", out).group(1))
    qos_out = [ln for ln in _section(out, "QOS").splitlines() if re.search(r"error|fail|not |invalid|unknown", ln, re.IGNORECASE)]
    if load_rc != 0:
        msgs = [ln.strip() for ln in _section(out, "LOAD").splitlines() if ln.strip()][:5] or [f"sacctmgr load exited {load_rc}"]
        return result(tool, shown, "failed", msgs + qos_out[:3])
    missing, changed = diff_dump(dump, _section(out, "AFTER"))
    if missing or qos_out:
        return result(tool, shown, "failed", missing[:8] + qos_out[:3], warnings=changed[:8])
    n_acc, n_usr = len(li.parse_sacctmgr_dump(dump)["accounts"]), len(li.parse_sacctmgr_dump(dump)["users"])
    return result(tool, shown, "passed", [f"loaded {n_acc} account(s), {n_usr} user(s), {len(qos_rows)} QOS; read back the same"], warnings=changed[:8])


# ---------------------------------------------------------------- promtool

def check_promtool(lab_dir: Path, ex: Executor, image: str = PROM_IMAGE) -> list[dict]:
    d = Path(lab_dir) / "baseline" / "rules"
    rule_files = sorted(p.name for p in d.glob("*.y*ml") if not re.search(r"\.test\.ya?ml$", p.name)) if d.is_dir() else []
    test_files = sorted(p.name for p in d.glob("*.test.y*ml")) if d.is_dir() else []

    def run(sub: list[str], files: list[str]) -> tuple[int, str]:
        return ex(["docker", "run", "--rm", "--network", "none", "--memory", "128m", "--entrypoint", "promtool", "-v", f"{d.resolve()}:/rules:ro",
                   image, *sub, *[f"/rules/{f}" for f in files]], timeout=ONE_SHOT_TIMEOUT_S)

    out = []
    for tool, sub, files, none in (("promtool check rules", ["check", "rules"], rule_files, "no rule files in this lab"),
                                   ("promtool test rules", ["test", "rules"], test_files, "no *.test.yml rule test in this lab")):
        shown = f"promtool {' '.join(sub)} {' '.join(files[:3])}{' ...' if len(files) > 3 else ''}"
        if not files:
            out.append(result(tool, shown, "skipped", [none]))
            continue
        rc, text = run(sub, files)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if rc == 0:
            out.append(result(tool, shown, "passed", [lines[-1] if lines else "ok"]))
        else:
            out.append(result(tool, shown, "failed", [re.sub(r"/rules/", "", ln) for ln in lines if "FAILED" in ln or "error" in ln.lower() or ln.startswith(("-", "  "))][:8]
                              or [re.sub(r"/rules/", "", _first_line(text))]))
    return out


# ---------------------------------------------------------------- kubectl

def check_kubernetes(lab_dir: Path, sim: dict, ex: Executor) -> dict:
    from . import k8s

    tool, shown = "kubectl dry-run", "kubectl apply --dry-run=server -f k8s/*.yaml"
    d = Path(lab_dir) / "baseline" / "k8s"
    files = sorted(d.glob("*.y*ml")) if d.is_dir() else []
    if not files:
        return result(tool, shown, "skipped", ["no Kubernetes manifests in this lab"])
    if not _running(ex, K8S_API):
        return result(tool, shown, "skipped", ["skipped: Kubernetes is not running (start it with `benchctl up --with k8s`)"])
    text = "\n---\n".join(f.read_text() for f in files)
    declared = {o["metadata"]["name"] for o in yaml.safe_load_all(text) if isinstance(o, dict) and o.get("kind") == "Namespace"}
    rc, out = ex(k8s.kubectl_cmd(sim, "apply", "--dry-run=server", "-f", "-", stdin=True), stdin=text, timeout=ONE_SHOT_TIMEOUT_S)
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    warn, errs = [], []
    for ln in lines:
        m = re.search(r'namespaces? "([^"]+)" not found', ln)
        if rc != 0 and m and m.group(1) in declared:
            warn.append(f"namespace {m.group(1)} is created by the same files, so its objects could not be dry-run")
        elif rc != 0 and re.search(r"error|invalid|forbidden|not found|unable|no matches", ln, re.IGNORECASE):
            errs.append(ln)
    if rc == 0:
        return result(tool, shown, "passed", [f"{len([ln for ln in lines if 'dry run' in ln])} object(s) accepted by the API server (server-side dry run)"])
    if errs or not warn:
        return result(tool, shown, "failed", (errs or [_first_line(out)])[:8], warnings=list(dict.fromkeys(warn)))
    return result(tool, shown, "passed", ["accepted by the API server, except objects in namespaces this lab creates"], warnings=list(dict.fromkeys(warn)))


# ---------------------------------------------------------------- Teleport

ROLE_VERSIONS = ("v3", "v4", "v5", "v6", "v7")  # certain for Teleport 18; a newer one is reported, not refused (Teleport decides when it runs)
ROLE_SPEC_KEYS = ("allow", "deny", "options")
ROLE_CONDITION_KEYS = (
    "logins", "node_labels", "node_labels_expression", "kubernetes_groups", "kubernetes_users", "kubernetes_labels", "kubernetes_labels_expression",
    "kubernetes_resources", "rules", "request", "review_requests", "app_labels", "app_labels_expression", "db_labels", "db_labels_expression",
    "db_names", "db_users", "db_roles", "db_service_labels", "windows_desktop_labels", "windows_desktop_labels_expression", "windows_desktop_logins",
    "cluster_labels", "cluster_labels_expression", "namespaces", "impersonate", "join_sessions", "spiffe", "aws_role_arns", "azure_identities",
    "gcp_service_accounts", "group_labels", "group_labels_expression", "host_group_labels", "workload_identity_labels", "github_permissions")
ROLE_OPTION_KEYS = (
    "forward_agent", "max_session_ttl", "port_forwarding", "ssh_port_forwarding", "client_idle_timeout", "disconnect_expired_cert", "permit_x11_forwarding",
    "request_access", "request_prompt", "max_connections", "max_sessions", "lock", "record_session", "desktop_clipboard", "desktop_directory_sharing",
    "pin_source_ip", "ssh_file_copy", "idp", "cert_format", "enhanced_recording", "create_desktop_user", "create_host_user", "create_host_user_mode",
    "create_host_user_default_shell", "create_db_user", "create_db_user_mode", "device_trust_mode", "require_session_join", "mfa_verification_interval",
    "ssh_allow_agent_forwarding", "cert_extensions", "ssh_x11_forwarding")


def _unknown(keys, known: tuple[str, ...], where: str, fail: list[str], warn: list[str]) -> None:
    for k in keys:
        if k in known:
            continue
        close = difflib.get_close_matches(str(k), known, n=1, cutoff=0.75)
        (fail if close else warn).append(f"{where}: unknown key {k!r}" + (f", did you mean {close[0]!r}?" if close else " (not known to this check)"))


def check_role_structure(role: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) of one role document: kind, version, metadata.name, spec.allow/deny, known keys with typos flagged."""
    fail: list[str] = []
    warn: list[str] = []
    name = (role.get("metadata") or {}).get("name") or "?"
    if role.get("kind") != "role":
        fail.append(f"{name}: kind must be `role`")
    v = str(role.get("version") or "")
    if not v:
        fail.append(f"{name}: no `version`")
    elif v not in ROLE_VERSIONS:
        (fail if not re.fullmatch(r"v\d+", v) else warn).append(f"{name}: version {v} is not one this lab knows for Teleport 18 ({', '.join(ROLE_VERSIONS)}); only a running Teleport can say")
    spec = role.get("spec")
    if not isinstance(spec, dict):
        return fail + [f"{name}: no `spec`"], warn
    _unknown(spec, ROLE_SPEC_KEYS, f"{name} spec", fail, warn)
    if not (isinstance(spec.get("allow"), dict) or isinstance(spec.get("deny"), dict)):
        fail.append(f"{name}: spec needs `allow` or `deny`")
    for side in ("allow", "deny"):
        if side in spec and not isinstance(spec[side], dict):
            fail.append(f"{name}: spec.{side} must be a mapping")
        elif isinstance(spec.get(side), dict):
            _unknown(spec[side], ROLE_CONDITION_KEYS, f"{name} spec.{side}", fail, warn)
    if isinstance(spec.get("options"), dict):
        _unknown(spec["options"], ROLE_OPTION_KEYS, f"{name} spec.options", fail, warn)
    elif "options" in spec:
        fail.append(f"{name}: spec.options must be a mapping")
    return fail, warn


def check_teleport(lab_dir: Path, ex: Executor) -> dict:
    tool = "teleport role"
    d = Path(lab_dir) / "baseline" / "teleport"
    files = sorted(d.glob("*.y*ml")) if d.is_dir() else []
    docs = [x for f in files for x in yaml.safe_load_all(f.read_text()) if isinstance(x, dict)]
    if not docs:
        return result(tool, "role structure check", "skipped", ["no Teleport roles in this lab"])
    fail: list[str] = []
    warn: list[str] = []
    for role in docs:
        f, w = check_role_structure(role)
        fail += f
        warn += w
    if fail:
        return result(tool, "role structure check", "failed", fail[:8], warnings=warn[:8], note="structure only")
    if not _running(ex, TELEPORT):
        return result(tool, "role structure check", "passed", [f"{len(docs)} role(s) have a valid structure; Teleport is not running, so Teleport itself did not look"],
                      warnings=warn[:8], note="structure only")
    # tctl create has no --dry-run (checked in `tctl create --help` of 18.11.2): create under a temporary name, read the verdict, delete at once
    tag, created, msgs = secrets.token_hex(3), [], []
    ok = True
    try:
        for i, role in enumerate(docs):
            tmp = {**role, "metadata": {**(role.get("metadata") or {}), "name": f"lab-validate-{tag}-{i}"}}
            rc, out = ex(["docker", "exec", "-i", TELEPORT, "tctl", "create"], stdin=yaml.safe_dump(tmp, sort_keys=False), timeout=30)
            if rc == 0:
                created.append(tmp["metadata"]["name"])
            else:
                ok = False
                msgs.append(f"{(role.get('metadata') or {}).get('name', '?')}: {_first_line(out)}")
    finally:
        for n in created:
            ex(["docker", "exec", TELEPORT, "tctl", "rm", f"role/{n}"], timeout=30)
    shown = "tctl create (temporary role lab-validate-*, deleted after)"
    if ok:
        return result(tool, shown, "passed", [f"{len(docs)} role(s) accepted by Teleport"], warnings=warn[:8], note="accepted by Teleport")
    return result(tool, shown, "failed", msgs[:8], warnings=warn[:8], note="rejected by Teleport")


# ---------------------------------------------------------------- secrets, versions

def check_secrets(lab_dir: Path) -> dict:
    files = {str(p.relative_to(lab_dir)): p.read_text() for sub in ("baseline", "render") for p in sorted((Path(lab_dir) / sub).rglob("*")) if p.is_file()}
    left = li.residual_secrets(files)
    if left:
        return result("secret scan", "scripts/check-privacy.sh rules over baseline/ and render/", "failed", [f"a secret may remain at {x}" for x in left[:8]])
    return result("secret scan", "scripts/check-privacy.sh rules over baseline/ and render/", "passed", [f"{len(files)} file(s) clean"])


def check_versions(lab_dir: Path) -> dict:
    m = json.loads((Path(lab_dir) / "manifest.json").read_text())
    match = m.get("version_match")
    if not match:
        return result("version check", "manifest.json", "passed", ["no workbench versions recorded; import again to compare"], note="unknown")
    diff = [f"{k}: production {m['versions'].get(k)} vs workbench {m['workbench_versions'].get(k)}" for k, s in match.items() if s == "differs"]
    unknown = [k for k, s in match.items() if s == "unknown"]
    msgs = ["every given version matches this workbench"] if not diff else [f"{x}; results for it are indicative" for x in diff]
    return result("version check", "manifest.json", "passed", msgs, warnings=[f"not compared: {', '.join(unknown)}"] if unknown else [],
                  note="indicative" if diff else None)


# ---------------------------------------------------------------- run and report

def summarise(results: list[dict]) -> str:
    failed = [r for r in results if r["status"] == "failed"]
    skipped = [r for r in results if r["status"] == "skipped"]
    if failed:
        return f"failed ({len(failed)} of {len(results)})"
    return "passed" if not skipped else f"passed with {len(skipped)} skipped"


def run(name: str, sim: dict, side: str = "baseline", ex: Executor = run_cmd, root: Path | None = None,
        secrets_dir: Path = Path("compose/generated/secrets")) -> dict:
    """Run every check; writes NAME/validate.json and returns it. `candidate` is not available until issue 553."""
    if side not in SIDES:
        raise LabError(f"--side must be baseline or candidate, not {side!r}")
    if side == "candidate":
        raise LabError("the candidate side is not available yet: it comes with issue 553 (diff and apply)")
    lab_dir = lab_store.path(name, root)
    if not lab_store.exists(name, root):
        raise LabError(f"no lab {name}: import it first (benchctl import lab {name} --from DIR)")
    results: list[dict] = []
    has_slurm = (lab_dir / "baseline" / "slurm.conf").is_file()
    if has_slurm:
        info = render(name, sim, root)
        results.append(check_slurm(Path(info["dir"]), info["host"], info["restart_keys"], ex))
        results[-1]["replaced_keys"] = info["replaced"]
    else:
        results.append(result("slurm.conf", "slurmctld -D", "skipped", ["no slurm.conf in this lab"]))
    results.append(check_accounting(lab_dir, ex, lab_dir / "validate-tmp", secrets_dir))
    shutil.rmtree(lab_dir / "validate-tmp", ignore_errors=True)
    results += check_promtool(lab_dir, ex)
    results.append(check_kubernetes(lab_dir, sim, ex))
    results.append(check_teleport(lab_dir, ex))
    results.append(check_secrets(lab_dir))
    results.append(check_versions(lab_dir))
    report = {"name": name, "side": side, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": results,
              "summary": summarise(results), "restart_required": any(r["restart_required"] for r in results)}
    (lab_dir / "validate.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def line(r: dict) -> str:
    """One line per result for the CLI, the page and the report."""
    mark = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}[r["status"]]
    msg = r["messages"][0] if r["messages"] else ""
    extra = (f" ({r['note']})" if r.get("note") else "") + (" [restart required]" if r.get("restart_required") else "")
    return f"{mark}  {r['tool']}: {msg}{extra}"


def text(report: dict) -> str:
    out = [f"validate lab {report['name']} ({report['side']}): {report['summary']}"]
    for r in report["results"]:
        out.append("  " + line(r))
        out += [f"        {m}" for m in r["messages"][1:]]
        out += [f"        warning: {w}" for w in r.get("warnings", [])]
    if any(r.get("replaced_keys") for r in report["results"]):
        keys = next(r["replaced_keys"] for r in report["results"] if r.get("replaced_keys"))
        out.append(f"  the lab replaced {len(keys)} production key(s) with its own (hosts, paths, auth; reported not tested): {', '.join(keys[:8])}{' ...' if len(keys) > 8 else ''}")
    return "\n".join(out)
