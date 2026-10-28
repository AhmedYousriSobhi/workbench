"""Generate slurm.conf and gres.conf from sim.yaml so node names agree everywhere (SPEC section 3)."""
from pathlib import Path

from .nodes import node_names

CTLD = "slurmctld"


def slurm_conf(sim: dict, compute_override: int | None = None) -> str:
    c, g = sim["compute"], sim.get("gpu", {})
    names = node_names(sim, compute_override)
    compute = [n for n, k in names if k == "compute"]
    gpu = [n for n, k in names if k == "gpu"]
    lines = [
        "# GENERATED from sim.yaml by `simctl compose`; do not edit.",
        "ClusterName=hpcsim",
        f"SlurmctldHost={CTLD}",
        "AuthType=auth/munge",
        "SlurmUser=slurm",
        "StateSaveLocation=/var/spool/slurmctld",
        "SlurmdSpoolDir=/var/spool/slurmd",
        "SlurmctldPidFile=/run/slurmctld.pid",
        "SlurmdPidFile=/run/slurmd.pid",
        # Containers have no real cgroup delegation or accounting: keep slurmd tiny.
        "ProctrackType=proctrack/linuxproc",
        "TaskPlugin=task/none",
        "JobAcctGatherType=jobacct_gather/none",
        "MailProg=/bin/true",
        "SlurmdParameters=config_overrides",
        "SelectType=select/cons_tres",
        "SelectTypeParameters=CR_Core_Memory",
        "ReturnToService=2",
        "SlurmctldTimeout=60",
        "SlurmdTimeout=120",
        "GresTypes=gpu",
    ]
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
    head = "# GENERATED; count-based fake GPUs\n"
    if not g.get("nodes"):
        return head
    gpu = [n for n, k in node_names(sim) if k == "gpu"]
    return head + f"NodeName={_range(gpu)} Name=gpu Type={g['type']} Count={g['gpus_per_node']}\n"


def _range(names: list[str]) -> str:
    """cn001..cn016 -> 'cn[001-016]' (comma-joined when not one run)."""
    if len(names) == 1:
        return names[0]
    prefix = names[0].rstrip("0123456789")
    nums = [n[len(prefix):] for n in names]
    return f"{prefix}[{nums[0]}-{nums[-1]}]"


def write(sim: dict, out_dir: Path, compute_override: int | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {"slurm.conf": slurm_conf(sim, compute_override), "gres.conf": gres_conf(sim),
             "cgroup.conf": "CgroupPlugin=autodetect\nIgnoreSystemd=yes\n"}
    for name, text in files.items():
        (out_dir / name).write_text(text)
    return [out_dir / n for n in files]
