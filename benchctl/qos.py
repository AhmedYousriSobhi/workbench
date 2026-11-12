"""Real Slurm QOS, multifactor priority and preemption from sim.yaml (issue 544, config lab A-6 and A-7).

    qos:                                   # real QOS objects in slurmdbd (sacctmgr); `normal` always exists
      - {name: scavenger, priority: 1, max_gpus_per_user: 2}
      - {name: urgent, priority: 1000, preempt: [scavenger], grace_time_s: 30}
    slurm:
      priority: {weight_qos: 1000, weight_age: 100, weight_fairshare: 10000, decay_half_life: 7-0}

`max_gpus_per_user` becomes MaxTRESPerUser=gres/gpu=N (a job over it waits with the real reason QOSMaxGRESPerUser), `preempt` lists the QOS a job of this one
may requeue (PreemptType=preempt/qos, PreemptMode=REQUEUE), `grace_time_s` is GraceTime. Every simulated user may submit under every declared QOS. QOS need
slurmdbd, so with `qos:` or `slurm.priority:` set and no accounting part the stack refuses to generate, with one message.
"""
from __future__ import annotations

import re


class QosError(ValueError):
    pass


QOS_KEYS = ("name", "priority", "max_gpus_per_user", "grace_time_s", "preempt")
WEIGHTS = {"weight_qos": "PriorityWeightQOS", "weight_age": "PriorityWeightAge", "weight_fairshare": "PriorityWeightFairShare",
           "weight_job_size": "PriorityWeightJobSize", "weight_partition": "PriorityWeightPartition"}
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,19}")
_HALF_LIFE = re.compile(r"\d+(-\d+)?(:\d+)?")  # 7-0 (days-hours) or 12:0 (hours:minutes)


def declared(sim: dict) -> list[dict]:
    return list(sim.get("qos") or [])


def priority(sim: dict) -> dict:
    return dict((sim.get("slurm") or {}).get("priority") or {})


def wanted(sim: dict) -> bool:
    return bool(declared(sim) or priority(sim))


def _whole(v, low: int = 0) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= low


def problems(sim: dict, accounting: bool | None = None) -> list[str]:
    """What is wrong with `qos:` and `slurm.priority:`; with `accounting` known (True or False) also whether slurmdbd is there for them."""
    out: list[str] = []
    items = declared(sim)
    names = [q.get("name") for q in items if isinstance(q, dict)]
    if not isinstance(sim.get("qos") or [], list) or len(names) != len(items):
        return ["qos must be a list of mappings with a name"]
    for q in items:
        n = q.get("name")
        if not isinstance(n, str) or not _NAME.fullmatch(n):
            out.append(f"qos name {n!r} must be lowercase letters, digits, - or _ (at most 20)")
            continue
        out += [f"qos {n}: unknown key {k!r} (one of {', '.join(QOS_KEYS)})" for k in q if k not in QOS_KEYS]
        out += [f"qos {n}: {k} must be a whole number of 0 or more" for k in ("priority", "max_gpus_per_user", "grace_time_s")
                if k in q and not _whole(q[k])]
        if "preempt" in q:
            p = q["preempt"]
            if not isinstance(p, list) or not p:
                out.append(f"qos {n}: preempt is a list of QOS names")
            else:
                out += [f"qos {n}: preempt names {x!r}, which is not declared (declared: {', '.join(names)}; normal is built in)"
                        for x in p if x not in names and x != "normal"]
                out += [f"qos {n}: cannot preempt itself" for x in p if x == n]
    out += [f"qos {n} is declared twice" for n in sorted({n for n in names if names.count(n) > 1})]
    for u in sim.get("users", []):
        q = u.get("qos", "normal")
        if items and q != "normal" and q not in names:
            out.append(f"user {u['name']}: qos {q!r} is not declared in qos:")
    pr = priority(sim)
    for k, v in pr.items():
        if k == "decay_half_life":
            if not isinstance(v, str) or not _HALF_LIFE.fullmatch(v) and v != "0":
                out.append("slurm.priority.decay_half_life must look like 7-0 (days-hours), 12:0 or 0 (no decay)")
        elif k not in WEIGHTS:
            out.append(f"slurm.priority: unknown key {k!r} (one of decay_half_life, {', '.join(WEIGHTS)})")
        elif not _whole(v):
            out.append(f"slurm.priority.{k} must be a whole number of 0 or more")
    if not out and accounting is False and wanted(sim):
        out.append("qos: and slurm.priority: need job accounting (slurmdbd): start with the accounting part, e.g. `benchctl up --with accounting`, "
                   "or remove them from sim.yaml")
    return out


def preempts(sim: dict) -> bool:
    return any(q.get("preempt") for q in declared(sim))


def slurm_lines(sim: dict) -> list[str]:
    """slurm.conf lines: multifactor priority (always, once QOS or priority is asked for) and QOS preemption when any QOS lists `preempt`."""
    if not wanted(sim):
        return []
    pr = priority(sim)
    lines = ["PriorityType=priority/multifactor"]
    if "decay_half_life" in pr:
        lines.append(f"PriorityDecayHalfLife={pr['decay_half_life']}")
    lines += [f"{slurm}={pr[key]}" for key, slurm in WEIGHTS.items() if key in pr]
    if declared(sim):
        lines.append("AccountingStorageEnforce=limits,qos")  # without it Slurm records the limits and enforces none; users need an association (accounting.sh makes them)
    if "weight_qos" not in pr and declared(sim):
        lines.append("PriorityWeightQOS=1000")  # without a weight a QOS priority changes nothing
    if any("max_gpus_per_user" in q for q in declared(sim)):
        lines.append("AccountingStorageTRES=gres/gpu")  # slurmdbd refuses a gres/gpu limit until the controller has registered the TRES
    if preempts(sim):
        lines += ["PreemptType=preempt/qos", "PreemptMode=REQUEUE"]
    return lines


def sacctmgr_lines(sim: dict) -> list[str]:
    """Idempotent: add the QOS (an existing one is kept), then set every field, so a changed sim.yaml reaches a database that already has it."""
    out: list[str] = []
    if any("max_gpus_per_user" in q for q in declared(sim)):  # slurmctld registers the TRES when it connects: wait for it (up to a minute)
        out.append("for i in $(seq 30); do sacctmgr -n show tres 2>/dev/null | grep -q gpu && break; sleep 2; done")
    for q in declared(sim):
        n = q["name"]
        sets = []
        if "priority" in q:
            sets.append(f"priority={q['priority']}")
        if "max_gpus_per_user" in q:
            sets.append(f"MaxTRESPerUser=gres/gpu={q['max_gpus_per_user']}")
        if "grace_time_s" in q:
            sets.append(f"GraceTime={q['grace_time_s']}")
        if "preempt" in q:
            sets.append(f"preempt={','.join(q['preempt'])}")
        out.append(f"sacctmgr -i add qos {n} || true")
        if sets:
            out.append(f"sacctmgr -i modify qos where name={n} set {' '.join(sets)}")
    return out


def user_qos(sim: dict, user: dict) -> list[str]:
    """The QOS a user may submit under: their own (the default) and every declared one."""
    own = user.get("qos", "normal")
    return [own, *[n for n in ["normal", *[q["name"] for q in declared(sim)]] if n != own]]
