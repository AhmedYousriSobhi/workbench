"""The Lab workspace's data (issue 556, W-17l, ADR 0049): read-only views of a config lab's files and the governor requests its buttons send.

Reads the lab folder (`compose/generated/lab/NAME`) and the run reports; never Docker, never the cluster (W-3). Writes are governor
requests only (`lab-apply`, `lab-restore`, `lab-run`, `lab-stop`), built here from checked input and sent by `app.py` with the CSRF header.
Import is CLI-only in v1.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchctl import lab, lab_candidate, lab_diff, lab_export, lab_run

ACTIONS = ("apply", "restore", "run", "stop")
STEPS = ("import", "validate", "diff", "apply", "replay", "report")


class LabRequestError(ValueError):
    pass


def _json(p: Path) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def runs(name: str, root: Path | None = None) -> list[dict]:
    """Every run of the lab, newest first: id, state, A/B, and whether it has a report and its verdict."""
    out = []
    base = lab_run.reports_dir(name, root)
    for f in sorted((p for p in base.glob("*") if p.is_dir()), reverse=True):
        rec = _json(f / "run.json") or {}
        rep = _json(f / "report.json") or {}
        out.append({"run": f.name, "state": rec.get("state"), "ab": rec.get("ab"), "reason": rec.get("reason"),
                    "report": bool(rep), "verdict": rep.get("verdict")})
    return out


def steps(have: dict[str, Any]) -> dict[str, str]:
    """The stepper: done | now | todo for each step, from what exists on disk and the governor's `lab` block."""
    run = have.get("run") or {}
    live = run.get("state") not in (None, "idle", "done", "aborted")
    out = {"import": "done",
           "validate": "done" if have.get("validated") else "todo",
           "diff": "done" if have.get("candidate") else "todo",
           "apply": "now" if have.get("applied") else "todo",
           "replay": "now" if live else ("done" if have.get("report") else "todo"),
           "report": "done" if have.get("report") else "todo"}
    return out


def summary(name: str, root: Path | None = None) -> dict:
    m = _json(lab.path(name, root) / "manifest.json") or {}
    return {"name": name, "files": len(m.get("files", [])), "created": m.get("created")}


def names(root: Path | None = None) -> list[str]:
    return lab.names(root)


def view(name: str, sim: dict, gov_lab: dict | None, root: Path | None = None) -> dict:
    """Everything the workspace shows for one lab. `gov_lab` is the governor's `status.lab` block (None: nothing applied, nothing running)."""
    if not lab.exists(name, root):
        raise LabRequestError(f"no lab {name}: import it first (benchctl import lab {name} --from DIR)")
    m = _json(lab.path(name, root) / "manifest.json") or {}
    ours = lab.workbench_versions(sim)
    try:
        plan = lab.plan(name, sim, root)
    except lab.LabError as e:
        plan = {"error": str(e)}
    cand = lab_candidate.has_candidate(name, root)
    try:
        diff = lab_diff.diff(name, root) if cand else []
    except lab.LabError as e:
        diff = [{"file": "candidate", "op": "changed", "key": None, "note": str(e)}]
    all_runs = runs(name, root)
    mine = gov_lab if gov_lab and gov_lab.get("name") == name else None
    have = {"validated": _json(lab.path(name, root) / "validate.json") is not None, "candidate": cand,
            "applied": bool(mine and mine.get("side")), "run": mine, "report": any(r["report"] for r in all_runs)}
    return {"name": name, "created": m.get("created"), "files": m.get("files", []), "versions": m.get("versions", {}),
            "version_match": lab.version_match(m.get("versions", {}), ours), "not_tested": m.get("not_tested", []),
            "scrubbed": m.get("scrubbed", []), "plan": plan, "candidate": cand, "diff": diff,
            "validation": _json(lab.path(name, root) / "validate.json"), "runs": all_runs, "governor": mine,
            "steps": steps(have), "applied": mine.get("side") if mine else None}


def report(name: str, run: str, anonymize: bool, root: Path | None = None) -> tuple[dict, str]:
    try:
        return lab_export.export(name, run, anonymize, root)
    except lab_export.ExportError as e:
        raise LabRequestError(str(e)) from e


def request(name: str, action: str, body: dict | None = None) -> dict:
    """The governor request for a button. Free text never reaches it: the side is one of two words, the length a number, the seed an int."""
    lab.valid_name(name)
    body = body or {}
    if action == "apply":
        side = body.get("side", "candidate")
        if side not in lab_candidate.SIDES:
            raise LabRequestError("side must be baseline or candidate")
        return {"cmd": "lab-apply", "name": name, "side": side}
    if action == "restore":
        return {"cmd": "lab-restore", "name": name}
    if action == "stop":
        return {"cmd": "lab-stop", "name": name, **({"force": True} if body.get("force") is True else {})}
    if action == "run":
        req: dict[str, Any] = {"cmd": "lab-run", "name": name, "ab": body.get("ab") is True}
        th = body.get("target_hours")
        if th is not None:
            if isinstance(th, bool) or not isinstance(th, (int, float)) or not 0.5 <= th <= 6:
                raise LabRequestError("target_hours must be a number from 0.5 to 6")
            req["target_hours"] = th
        return req
    raise LabRequestError(f"no such action {action!r}")
