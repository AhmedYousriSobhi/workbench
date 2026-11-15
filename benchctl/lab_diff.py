"""Config lab diff (issue 553, ADR 0049): candidate against baseline, one note per changed line.

slurm.conf is compared by key (`lab_slurm.KEY_CLASS` says what each change costs), every other file by line. Notes:
  live                       applied by the lab with no restart
  restart                    applied; slurmctld restarts
  not applied: unknown       key not in any class (ADR 0049 addendum, issue 553); listed in the report
  not tested: lab-owned      the base value stays
  not applied: structure     NodeName / PartitionName / topology / gres: the lab's node set comes with the run (issue 554)
  replay only                the trace feeds the replay, nothing is applied
"""
from __future__ import annotations

import difflib

from . import lab_candidate, lab_import, lab_slurm
from .lab import LabError

STRUCTURE_FILES = {"topology.conf", "gres.conf"}
STRUCT_NOTE = "not applied: structure (with the run)"


def key_note(key: str) -> str:
    c = lab_slurm.classify(key)
    if c == "unknown":
        return "not applied: unknown"
    if c == "lab-owned":
        return "not tested: lab-owned, the base value stays"
    if lab_slurm.restart_required(key):
        return "restart"
    return "live (time unscaled)" if c == "time-scaled" else "live"


def _keys(text: str | None) -> dict[str, str]:
    return lab_import.parse_slurm_conf(text)["keys"] if text else {}


def _struct(text: str | None) -> list[str]:
    if not text:
        return []
    p = lab_import.parse_slurm_conf(text)
    return sorted(" ".join(f"{k}={v}" for k, v in row.items()) for row in p["nodes"] + p["partitions"] + p["other"])


def _file_note(rel: str) -> str:
    if rel in STRUCTURE_FILES:
        return STRUCT_NOTE
    if rel == "trace.sacct":
        return "replay only"
    return "live"


def diff(name: str, root=None) -> list[dict]:
    """[{file, op: added|removed|changed, key, from, to, note}] of the candidate against the baseline."""
    base, cand = lab_candidate.effective(name, "baseline", root), lab_candidate.effective(name, "candidate", root)
    out: list[dict] = []
    for rel in sorted(set(base) | set(cand)):
        b, c = base.get(rel), cand.get(rel)
        if b == c:
            continue
        if rel == "slurm.conf":
            try:
                kb, kc = _keys(b), _keys(c)
                sb, sc = _struct(b), _struct(c)
            except lab_import.ImportError_ as e:
                raise LabError(f"candidate slurm.conf: {e}") from e
            for k in sorted(set(kb) | set(kc)):
                if kb.get(k) != kc.get(k):
                    out.append({"file": rel, "op": "added" if k not in kb else "removed" if k not in kc else "changed", "key": k,
                                "from": kb.get(k), "to": kc.get(k), "note": key_note(k) if k in kc else "removed: the base value returns"})
            for line in sorted(set(sb) ^ set(sc)):
                out.append({"file": rel, "op": "added" if line in sc else "removed", "key": None, "from": None if line in sc else line,
                            "to": line if line in sc else None, "note": STRUCT_NOTE})
            continue
        if b is None or c is None:
            out.append({"file": rel, "op": "added" if b is None else "removed", "key": None, "from": None, "to": None, "note": _file_note(rel)})
            continue
        for ln in difflib.unified_diff(b.splitlines(), c.splitlines(), lineterm="", n=0):
            if ln.startswith(("+++", "---", "@@")):
                continue
            out.append({"file": rel, "op": "added" if ln[0] == "+" else "removed", "key": None,
                        "from": ln[1:] if ln[0] == "-" else None, "to": ln[1:] if ln[0] == "+" else None, "note": _file_note(rel)})
    return out


def restart_keys(entries: list[dict]) -> list[str]:
    """slurm.conf keys of a diff whose change restarts slurmctld (never an unknown key: it is not applied)."""
    return [e["key"] for e in entries if e["file"] == "slurm.conf" and e["key"] and e["to"] is not None
            and lab_slurm.classify(e["key"]) not in ("unknown", "lab-owned") and lab_slurm.restart_required(e["key"])]


def text(name: str, entries: list[dict]) -> str:
    if not entries:
        return f"lab {name}: the candidate equals the baseline"
    lines = [f"lab {name}: candidate against baseline, {len(entries)} change(s)"]
    cur = None
    for e in entries:
        if e["file"] != cur:
            cur = e["file"]
            lines.append(cur)
        if e["key"] and e["op"] == "changed":
            what = f"{e['key']}: {e['from']} -> {e['to']}"
        elif e["key"]:
            what = f"{e['key']}={e['to'] if e['op'] == 'added' else e['from']}"
        else:
            what = (e["to"] if e["op"] == "added" else e["from"]) or "(whole file)"
        sign = {"added": "+", "removed": "-", "changed": "~"}[e["op"]]
        lines.append(f"  {sign} {what}  [{e['note']}]")
    rk = restart_keys(entries)
    lines.append("apply: slurmctld " + (f"restarts ({', '.join(rk)})" if rk else "reconfigures (no restart)"))
    return "\n".join(lines)
