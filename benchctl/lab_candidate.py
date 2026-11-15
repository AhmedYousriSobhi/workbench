"""Config lab candidate overlay (issue 553, ADR 0049): the operator's edits next to the imported baseline.

compose/generated/lab/NAME/baseline/   what `import lab` stored (scrubbed)
compose/generated/lab/NAME/candidate/  the operator's edits, same file names; a file missing here means "as the baseline"
compose/generated/lab/NAME/render/     what was applied, for audit (written by lab_apply)

The effective files of a side are the baseline's, with every file present in `candidate/` replacing its baseline namesake.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import lab
from .lab import LabError

SIDES = ("baseline", "candidate")


def check_side(side: str) -> str:
    if side not in SIDES:
        raise LabError(f"side must be baseline or candidate, not {side!r}")
    return side


def _files(folder: Path) -> dict[str, Path]:
    return {str(p.relative_to(folder)): p for p in sorted(folder.rglob("*")) if p.is_file()} if folder.is_dir() else {}


def init(name: str, root: Path | None = None, replace: bool = False) -> list[str]:
    """Copy the baseline into `candidate/` so it can be edited; returns the file names. Refuses to overwrite edits."""
    if not lab.exists(name, root):
        raise LabError(f"no lab {name}: import it first (benchctl import lab {name} --from DIR)")
    base, cand = lab.path(name, root) / "baseline", lab.path(name, root) / "candidate"
    if cand.exists() and not replace:
        raise LabError(f"lab {name} already has a candidate: edit the files in compose/generated/lab/{name}/candidate, or add --replace to start again from the baseline")
    shutil.rmtree(cand, ignore_errors=True)
    shutil.copytree(base, cand)
    return sorted(_files(cand))


def has_candidate(name: str, root: Path | None = None) -> bool:
    return bool(_files(lab.path(name, root) / "candidate"))


def effective(name: str, side: str, root: Path | None = None) -> dict[str, str]:
    """{relative name: text} of the side's files. The candidate side needs a candidate."""
    check_side(side)
    if not lab.exists(name, root):
        raise LabError(f"no lab {name}: import it first (benchctl import lab {name} --from DIR)")
    folder = lab.path(name, root)
    out = {rel: p.read_text() for rel, p in _files(folder / "baseline").items()}
    if side == "candidate":
        cand = _files(folder / "candidate")
        if not cand:
            raise LabError(f"lab {name} has no candidate: create one with `benchctl add candidate {name}`, edit it, then try again")
        out.update({rel: p.read_text() for rel, p in cand.items()})
    return out
