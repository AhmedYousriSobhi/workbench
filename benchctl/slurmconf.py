"""Generate slurm.conf and gres.conf from sim.yaml so node names agree everywhere (SPEC section 3)."""
import secrets
from pathlib import Path

from .nodes import node_names

CTLD = "slurmctld"


def slurm_conf(sim: dict, compute_override: int | None = None, accounting: bool = False) -> str:
    c, g = sim["compute"], sim.get("gpu", {})
    names = node_names(sim, compute_override)
    compute = [n for n, k in names if k == "compute"]
    gpu = [n for n, k in names if k == "gpu"]
    lines = [
        "# GENERATED from sim.yaml by `benchctl compose`; do not edit.",
        "ClusterName=workbench",
        f"SlurmctldHost={CTLD}",
        "AuthType=auth/munge",
        "SlurmUser=slurm",
        "StateSaveLocation=/var/spool/slurmctld",
        "SlurmdSpoolDir=/var/spool/slurmd",
        "SlurmctldPidFile=/run/slurmctld.pid",
        "SlurmdPidFile=/run/slurmd.pid",
        # Jobs are synthetic and containers get no cgroup write access (26.05 accepts CgroupPlugin=disabled).
        "ProctrackType=proctrack/linuxproc",
        "TaskPlugin=task/none",
        "JobAcctGatherType=jobacct_gather/none",
        "MailProg=/bin/true",
        "SlurmdParameters=config_overrides",
        "SelectType=select/cons_tres",
        "SelectTypeParameters=CR_Core_Memory",
        "ReturnToService=1",  # 2 would return a node set DOWN by an admin (Power off) as soon as slurmd answers a ping
        "SlurmctldTimeout=60",
        "SlurmdTimeout=15",  # a node that stops answering is DOWN* (Not responding) after about 30 s
        "MessageTimeout=5",  # the default 10 made each ping to a frozen node block an agent for 21 s: DOWN took ~85 s (#154)
        "GresTypes=gpu",
        # Node health check (images/node-base/nhc): drains a node whose GPU count, XID state, IB ports or NIC are wrong.
        "HealthCheckProgram=/usr/local/sbin/nhc", "HealthCheckInterval=30", "HealthCheckNodeState=ANY",
        # The same checks before a job starts (a failure drains the node and requeues the job) and after it ends.
        "Prolog=/usr/local/sbin/nhc-prolog", "Epilog=/usr/local/sbin/nhc-epilog",
        # slurmrestd authenticates callers with JWT; the key is baked into the lab image (ADR 0002).
        "AuthAltTypes=auth/jwt",
        "AuthAltParameters=jwt_key=/var/spool/slurmctld/jwt_hs256.key",
    ]
    if accounting:
        lines += ["AccountingStorageType=accounting_storage/slurmdbd", "AccountingStorageHost=slurmdbd"]
    if compute:
        lines.append(f"NodeName={_range(compute)} CPUs={c['cpus']} RealMemory={c['memory_mb']} State=UNKNOWN")
    if gpu:
        gres = f"gpu:{g['type']}:{g['gpus_per_node']}"
        lines.append(f"NodeName={_range(gpu)} CPUs={c['cpus']} RealMemory={c['memory_mb']} "
                     f"Gres={gres} State=UNKNOWN")
    for p in sim.get("partitions", []):
        members = compute if p["nodes"] == "compute" else gpu
        if members:
            default = " Default=YES" if p is sim["partitions"][0] else ""
            lines.append(f"PartitionName={p['name']} Nodes={_range(members)}{default} State=UP")
    return "\n".join(lines) + "\n"


def gres_conf(sim: dict) -> str:
    """Count-only fake GPUs need no /dev files; only the GPU nodes get a line."""
    g = sim.get("gpu", {})
    head = "# GENERATED; fake GPUs backed by stand-in device files\n"
    if not g.get("nodes"):
        return head
    gpu = [n for n, k in node_names(sim) if k == "gpu"]
    last = g["gpus_per_node"] - 1
    files = f"/dev/workbench-gpu[0-{last}]" if last else "/dev/workbench-gpu0"
    return head + f"NodeName={_range(gpu)} Name=gpu Type={g['type']} File={files}\n"


def _range(names: list[str]) -> str:
    """cn001..cn016 -> 'cn[001-016]' (comma-joined when not one run)."""
    if len(names) == 1:
        return names[0]
    prefix = names[0].rstrip("0123456789")
    nums = [n[len(prefix):] for n in names]
    return f"{prefix}[{nums[0]}-{nums[-1]}]"


def slurmdbd_conf(password: str) -> str:
    return "\n".join([
        "# GENERATED; contains a secret, never commit", "AuthType=auth/munge", "DbdHost=slurmdbd",
        "AuthAltTypes=auth/jwt", "AuthAltParameters=jwt_key=/var/spool/slurmctld/jwt_hs256.key",
        "SlurmUser=slurm", "LogFile=/dev/stderr", "PidFile=/run/slurmdbd.pid",
        "StorageType=accounting_storage/mysql", "StorageHost=mariadb", "StorageUser=slurm",
        f"StoragePass={password}", "StorageLoc=slurm_acct_db", ""])


def accounting_script(sim: dict) -> str:
    """Idempotent sacctmgr bootstrap: cluster, QOS, one account, users from sim.yaml."""
    users = sim.get("users", [])
    qos = sorted({u["qos"] for u in users if u["qos"] != "normal"})
    lines = ["#!/bin/sh", "# GENERATED from sim.yaml; safe to re-run",
             "until sacctmgr -i show cluster >/dev/null 2>&1; do sleep 2; done",
             "sacctmgr -i add cluster workbench || true", "sacctmgr -i add account sim || true"]
    lines += [f"sacctmgr -i add qos {q} || true" for q in qos]
    lines += [f"sacctmgr -i add user {u['name']} account=sim qos={u['qos']} || true" for u in users]
    return "\n".join(lines) + "\n"


def write(sim: dict, out_dir: Path, compute_override: int | None = None,
          accounting: bool = False) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {"slurm.conf": slurm_conf(sim, compute_override, accounting), "gres.conf": gres_conf(sim),
             "cgroup.conf": "CgroupPlugin=disabled\n"}
    if accounting:
        files["accounting.sh"] = accounting_script(sim)
    for name, text in files.items():
        (out_dir / name).write_text(text)
    if accounting:
        _write_secrets(sim, out_dir.parent / "secrets", out_dir)
    return [out_dir / n for n in files]


def _write_secrets(sim: dict, sec: Path, slurm_dir: Path) -> None:
    """DB credentials live only in the gitignored generated tree; an existing password is kept so a
    running database keeps matching after a regeneration."""
    env = sec / "mariadb.env"
    sec.mkdir(parents=True, exist_ok=True)
    pw = None
    if env.exists():
        pw = next((ln.split("=", 1)[1] for ln in env.read_text().splitlines()
                   if ln.startswith("MARIADB_PASSWORD=")), None)
    pw = pw or secrets.token_urlsafe(24)
    env.write_text(f"MARIADB_RANDOM_ROOT_PASSWORD=yes\nMARIADB_USER=slurm\nMARIADB_PASSWORD={pw}\n"
                   "MARIADB_DATABASE=slurm_acct_db\n")
    etc = sec / "slurmdbd"
    etc.mkdir(exist_ok=True)
    for d in (sec, etc):
        d.chmod(0o700)
    (etc / "slurmdbd.conf").write_text(slurmdbd_conf(pw))
    (etc / "slurm.conf").write_text((slurm_dir / "slurm.conf").read_text())
    for f in (env, etc / "slurmdbd.conf"):
        f.chmod(0o600)
