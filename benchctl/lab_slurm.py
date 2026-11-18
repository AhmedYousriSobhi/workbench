"""Config lab, Slurm side (issue 551, ADR 0049 and 0050): key classes, time scaling, shape-preserving downscale.

`KEY_CLASS` says what the lab does with each slurm.conf key of the imported baseline:
  policy       applied as given, live with `scontrol reconfigure`
  time-scaled  applied with its time divided by the dilation factor (same factor in both runs), live
  restart      applied, needs a slurmctld restart
  lab-owned    the base value stays (hosts, paths, auth, privileged plugins); reported "not tested"
  unknown      applied and flagged; restarts slurmctld (a key ending in Type restarts too)
The downscale keeps every (partition x node shape x topology leaf) group alive with at least one node and splits the rest of
`lab.max_nodes` in proportion; production node names are kept.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from .lab_import import ImportError_ as LabError
from .lab_import import expand_hostlist

DEFAULT_MAX_NODES, HARD_MAX_NODES = 24, 64
DEFAULT_MAX_RUNNING_JOBS = 50
DEFAULT_TARGET_HOURS, MIN_TARGET_HOURS, MAX_TARGET_HOURS = 2.0, 0.5, 6.0
INDICATIVE_RATIO = 100  # production nodes per lab node above which results are indicative (ADR 0050)
LAB_NODE_MB = 13  # one lab node container (ADR 0049 consequences)
REPLAY_MB = 250  # the replay and validation one-shots (ADR 0050, GV-9)

# ---------------------------------------------------------------- key classes

_POLICY = ["PriorityWeightAge", "PriorityWeightAssoc", "PriorityWeightFairshare", "PriorityWeightJobSize", "PriorityWeightPartition", "PriorityWeightQOS", "PriorityWeightTRES", "PriorityFavorSmall", "PriorityFlags", "PriorityUsageResetPeriod", "PreemptMode", "SchedulerParameters", "SelectTypeParameters", "FairShareDampeningFactor", "DefMemPerCPU", "DefMemPerNode", "DefMemPerGPU", "DefCpuPerGPU", "MaxMemPerCPU", "MaxMemPerNode", "MaxJobCount", "MaxArraySize", "MaxStepCount", "MaxTasksPerNode", "EnforcePartLimits", "AccountingStorageEnforce", "AccountingStorageTRES", "TopologyParam", "ReturnToService", "PriorityParameters", "PreemptParameters", "DefaultStorageType", "LaunchParameters", "CpuFreqDef"]
_TIME_SCALED = {  # key -> unit of its bare numbers: s, min, or time (a Slurm time string such as 7-0 or 12:00:00)
    "PriorityDecayHalfLife": "time", "PriorityMaxAge": "time", "PreemptExemptTime": "time", "PriorityCalcPeriod": "min",
    "OverTimeLimit": "min", "MinJobAge": "s", "KillWait": "s", "SchedulerTimeSlice": "s", "InactiveLimit": "s", "WaitTime": "s"}
_RESTART = ["GresTypes", "TopologyPlugin", "PriorityType", "PreemptType", "SchedulerType", "SelectType", "JobCompType", "MpiDefault", "TaskPlugin", "SwitchType", "NodeFeaturesPlugins", "LaunchType"]
_LAB_OWNED = ["ClusterName", "SlurmctldHost", "SlurmctldAddr", "BackupController", "BackupAddr", "ControlMachine", "ControlAddr", "AuthType", "AuthInfo", "AuthAltTypes", "AuthAltParameters", "CredType", "SlurmUser", "SlurmdUser", "StateSaveLocation", "SlurmdSpoolDir", "SlurmctldPidFile", "SlurmdPidFile", "SlurmctldLogFile", "SlurmdLogFile", "SlurmctldPort", "SlurmdPort", "SlurmctldDebug", "SlurmdDebug", "SlurmctldParameters", "SlurmdParameters", "ProctrackType", "JobAcctGatherType", "JobAcctGatherFrequency", "AccountingStorageType", "AccountingStorageHost", "AccountingStoragePort", "AccountingStorageUser", "AccountingStoragePass", "StorageType", "StorageHost", "StoragePort", "StorageUser", "StoragePass", "StorageLoc", "JwtKey", "MailProg", "HealthCheckProgram", "HealthCheckInterval", "HealthCheckNodeState", "Prolog", "Epilog", "PrologSlurmctld", "EpilogSlurmctld", "TaskProlog", "TaskEpilog", "SrunProlog", "SrunEpilog", "PrologFlags", "SuspendProgram", "ResumeProgram", "RebootProgram", "SuspendTime", "SuspendTimeout", "ResumeTimeout", "JobSubmitPlugins", "CgroupPlugin", "PluginDir", "MetricsType", "SlurmctldTimeout", "SlurmdTimeout", "MessageTimeout", "BatchStartTimeout", "CompleteWait", "UnkillableStepTimeout", "UnkillableStepProgram", "LogTimeFormat", "SlurmSchedLogFile", "SlurmSchedLogLevel", "JobCompHost", "JobCompLoc", "JobCompPass", "JobCompUser", "TmpFS", "PrivateData"]

KEY_CLASS: dict[str, str] = {**dict.fromkeys(_POLICY, "policy"), **dict.fromkeys(_TIME_SCALED, "time-scaled"),
                             **dict.fromkeys(_RESTART, "restart"), **dict.fromkeys(_LAB_OWNED, "lab-owned")}
_LOWER = {k.lower(): k for k in KEY_CLASS}
CLASSES = ("policy", "time-scaled", "restart", "lab-owned", "unknown")

# SchedulerParameters options that are durations: option -> unit of its number
SCHED_TIME_OPTIONS = {"bf_interval": "s", "bf_resolution": "s", "bf_window": "min", "bf_max_time": "s", "sched_interval": "s",
                      "batch_sched_delay": "s"}


def classify(key: str) -> str:
    """The class of a slurm.conf key (Slurm keys are case-insensitive)."""
    canon = _LOWER.get(key.lower())
    return KEY_CLASS[canon] if canon else "unknown"


def restart_required(key: str) -> bool:
    """slurmctld must restart (not `scontrol reconfigure`): the restart class, a key ending in Type, or an unknown key."""
    c = classify(key)
    return c == "restart" or c == "unknown" or key.lower().endswith("type")


def classify_keys(keys: dict[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {c: [] for c in CLASSES}
    for k in sorted(keys):
        out[classify(k)].append(k)
    return out


# ---------------------------------------------------------------- Slurm durations and time scaling

_UNLIMITED = ("INFINITE", "UNLIMITED", "NONE", "PARTITION_LIMIT", "NOT_SET", "")


def parse_duration(text: str, bare: str = "min") -> int | None:
    """A Slurm time string in seconds: `M`, `M:S`, `H:M:S`, `D-H`, `D-H:M`, `D-H:M:S`. A bare number is `bare` (min or s). None for INFINITE and friends."""
    t = text.strip()
    if t.upper() in _UNLIMITED:
        return None
    days = 0
    if "-" in t:
        d, _, t = t.partition("-")
        if not d.isdigit():
            raise LabError(f"bad time {text!r}")
        days = int(d)
    parts = t.split(":")
    if not all(p.replace(".", "", 1).isdigit() for p in parts) or len(parts) > 3:
        raise LabError(f"bad time {text!r}")
    nums = [float(p) for p in parts]
    if days or "-" in text:  # D-H[:M[:S]]
        mult = [3600, 60, 1]
    elif len(nums) == 1:
        mult = [60 if bare == "min" else 1]
    elif len(nums) == 2:
        mult = [60, 1]
    else:
        mult = [3600, 60, 1]
    return round(days * 86400 + sum(n * m for n, m in zip(nums, mult)))


def format_duration(seconds: int) -> str:
    """H:MM:SS, with days as D-HH:MM:SS (Slurm accepts both everywhere a time is read)."""
    d, rest = divmod(max(0, int(seconds)), 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    return f"{d}-{h:02d}:{m:02d}:{s:02d}" if d else f"{h:02d}:{m:02d}:{s:02d}"


def scale_seconds(seconds: int, factor: float) -> int:
    """Dilate a duration: divide by the factor, never below one second."""
    return max(1, round(seconds / factor))


def scale_time_value(value: str, unit: str, factor: float) -> str:
    """Scale a configuration value of `unit` (s, min, time); INFINITE and friends stay."""
    if value.strip().upper() in _UNLIMITED:
        return value
    if unit == "s":
        return str(scale_seconds(int(float(value)), factor))
    if unit == "min":  # whole minutes are all these keys take: at least 1
        return str(max(1, round(float(value) / factor)))
    return format_duration(scale_seconds(parse_duration(value) or 0, factor))


def scale_scheduler_parameters(value: str, factor: float) -> str:
    out = []
    for item in value.split(","):
        k, eq, v = item.partition("=")
        unit = SCHED_TIME_OPTIONS.get(k)
        if eq and unit and v.isdigit():
            v = str(max(1, round(int(v) / factor))) if unit == "min" else str(scale_seconds(int(v), factor))
        out.append(f"{k}{eq}{v}" if eq else k)
    return ",".join(out)


def dilate_keys(keys: dict[str, str], factor: float) -> tuple[dict[str, str], list[dict[str, str]]]:
    """(keys with every time-based parameter divided by the factor, the list of what changed: key, from, to). Listed in the report."""
    out, changed = dict(keys), []
    for k, v in keys.items():
        canon = _LOWER.get(k.lower())
        new = v
        if canon in _TIME_SCALED:
            new = scale_time_value(v, _TIME_SCALED[canon], factor)
        elif canon == "SchedulerParameters":
            new = scale_scheduler_parameters(v, factor)
        if new != v:
            out[k] = new
            changed.append({"key": k, "from": v, "to": new})
    return out, changed


def dilate_partition(p: dict[str, str], factor: float) -> dict[str, str]:
    """MaxTime and DefaultTime of a partition (jobs' sleeps and limits shrink together)."""
    out = dict(p)
    for k in ("MaxTime", "DefaultTime"):
        if k in out and out[k].upper() not in _UNLIMITED:
            out[k] = format_duration(scale_seconds(parse_duration(out[k]) or 0, factor))
    return out


def dilate_qos(row: dict[str, str], factor: float) -> dict[str, str]:
    """GraceTime (HH:MM:SS) and MaxWall of a QOS export row."""
    out = dict(row)
    for k in ("GraceTime", "MaxWall", "MaxWallDurationPerJob"):
        v = out.get(k, "")
        if v and v.upper() not in _UNLIMITED and parse_duration(v, "s" if k == "GraceTime" else "min"):
            out[k] = format_duration(scale_seconds(parse_duration(v, "s" if k == "GraceTime" else "min"), factor))
    return out


# ---------------------------------------------------------------- nodes, shapes, leaves

_NOT_SHAPE = {"NodeName", "State", "Reason", "NodeAddr", "NodeHostname", "Port"}


def shape_of(node_line: dict[str, str], gres_types: list[str]) -> str:
    """A short id for a node's hardware: its NodeName line without names and state, plus its gres.conf types."""
    items = sorted((k, v) for k, v in node_line.items() if k not in _NOT_SHAPE)
    raw = repr(items) + repr(sorted(gres_types))
    return hashlib.sha1(raw.encode()).hexdigest()[:6]


def _gres_types(gres_rows: list[dict[str, str]], name: str) -> list[str]:
    out = []
    for r in gres_rows:
        if "NodeName" in r and name not in expand_hostlist(r["NodeName"]):
            continue
        out.append(f"{r.get('Name', '')}:{r.get('Type', '')}:{r.get('Count', '')}")
    return out


def production_nodes(slurm: dict[str, Any], topology: list[dict[str, str]], gres: list[dict[str, str]]) -> list[dict[str, Any]]:
    """[{name, shape, partitions, leaf}] in production order."""
    sets = {o["NodeSet"]: o.get("Nodes", "") for o in slurm["other"] if "NodeSet" in o}
    nodes: dict[str, dict[str, str]] = {}
    for line in slurm["nodes"]:
        for n in expand_hostlist(line["NodeName"]):
            nodes[n] = line
    if not nodes:
        raise LabError("slurm.conf declares no NodeName")

    def members(spec: str) -> list[str]:
        if spec.upper() == "ALL":
            return list(nodes)
        out = []
        for part in _split_commas(spec):
            out += members(sets[part]) if part in sets else expand_hostlist(part)
        return out

    part_of: dict[str, list[str]] = defaultdict(list)
    for p in slurm["partitions"]:
        for n in members(p.get("Nodes", "")):
            if n not in nodes:
                raise LabError(f"partition {p['PartitionName']} lists node {n}, which slurm.conf does not declare")
            part_of[n].append(p["PartitionName"])
    leaf_of: dict[str, str] = {}
    for row in topology:
        label = row.get("SwitchName") or row.get("BlockName")
        if "Nodes" in row and label:
            for n in expand_hostlist(row["Nodes"]):
                leaf_of.setdefault(n, label)
    return [{"name": n, "shape": shape_of(line, _gres_types(gres, n)), "partitions": sorted(part_of.get(n, [])), "leaf": leaf_of.get(n)}
            for n, line in nodes.items()]


def _split_commas(spec: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in spec:
        depth += ch == "["
        depth -= ch == "]"
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur] if cur else out


def _split_in_proportion(counts: list[int], total: int) -> list[int]:
    """Give each group 1, then share the rest in proportion to the production counts (largest remainder, capped at each count)."""
    lab = [1] * len(counts)
    extra = total - len(counts)
    while extra > 0:
        room = [c - a for c, a in zip(counts, lab)]
        weight = sum(c for c, r in zip(counts, room) if r > 0)
        if weight == 0:
            break
        quota = [(c * extra / weight if r > 0 else 0.0) for c, r in zip(counts, room)]
        give = [min(int(q), r) for q, r in zip(quota, room)]
        left = extra - sum(give)
        order = sorted(range(len(counts)), key=lambda i: (-(quota[i] - int(quota[i])), i))
        for i in order:
            if left == 0:
                break
            if give[i] < room[i]:
                give[i] += 1
                left -= 1
        if not any(give):
            break
        lab = [a + g for a, g in zip(lab, give)]
        extra -= sum(give)
    return lab


def downscale(nodes: list[dict[str, Any]], max_nodes: int = DEFAULT_MAX_NODES) -> dict[str, Any]:
    """Keep at least one node per (partitions x shape x leaf) group, split the rest of `max_nodes` in proportion. Production names stay."""
    if not 1 <= max_nodes <= HARD_MAX_NODES:
        raise LabError(f"lab.max_nodes must be from 1 to {HARD_MAX_NODES}, not {max_nodes}")
    groups: dict[tuple, list[str]] = {}
    for n in nodes:
        groups.setdefault((tuple(n["partitions"]), n["shape"], n["leaf"]), []).append(n["name"])
    total = min(max_nodes, len(nodes))
    if len(groups) > total:
        need = len(groups)
        hint = (f"raise lab.max_nodes to {need}" if need <= HARD_MAX_NODES else f"the cluster has {need} node groups, more than the {HARD_MAX_NODES} nodes a lab can hold; "
                "drop a partition, shape or switch from the export")
        raise LabError(f"cannot keep every (partition x shape x leaf) group with {max_nodes} nodes: {need} groups; {hint}")
    keys = list(groups)
    sizes = _split_in_proportion([len(groups[k]) for k in keys], total)
    out_groups, kept = [], []
    for k, size in zip(keys, sizes):
        names = groups[k][:size]
        kept += names
        out_groups.append({"partitions": list(k[0]), "shape": k[1], "leaf": k[2], "production": len(groups[k]), "lab": size, "nodes": names})
    order = {n["name"]: i for i, n in enumerate(nodes)}
    kept.sort(key=order.get)
    ratio = len(nodes) / len(kept)
    return {"production_nodes": len(nodes), "lab_nodes": len(kept), "ratio": ratio, "indicative": ratio > INDICATIVE_RATIO,
            "groups": out_groups, "nodes": kept, "services": {n: f"lab-node-{i:03d}" for i, n in enumerate(kept, 1)}}


def admission_mb(lab_nodes: int) -> int:
    """What the governor's admit must find free for a lab run: the lab node containers plus the replay's cap (ADR 0049, 0050, GV-9)."""
    return lab_nodes * LAB_NODE_MB + REPLAY_MB


# ---------------------------------------------------------------- settings (sim.yaml `lab:`)

def settings(sim: dict) -> dict:
    return {"max_nodes": DEFAULT_MAX_NODES, "max_running_jobs": DEFAULT_MAX_RUNNING_JOBS, "target_hours": DEFAULT_TARGET_HOURS,
            **(sim.get("lab") or {})}


def problems(sim: dict) -> list[str]:
    s = settings(sim)
    out = []
    for key, top in (("max_nodes", HARD_MAX_NODES), ("max_running_jobs", 10000)):
        if not isinstance(s[key], int) or isinstance(s[key], bool) or not 1 <= s[key] <= top:
            out.append(f"lab.{key} must be a whole number from 1 to {top}")
    if not isinstance(s["target_hours"], (int, float)) or isinstance(s["target_hours"], bool) or not MIN_TARGET_HOURS <= s["target_hours"] <= MAX_TARGET_HOURS:
        out.append(f"lab.target_hours must be from {MIN_TARGET_HOURS} to {MAX_TARGET_HOURS}")
    from . import (
        lab_report,  # late: lab_report does not import this module, but it is only needed here
    )
    return out + lab_report.problems(sim)
