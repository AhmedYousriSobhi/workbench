"""`benchctl export lab-report NAME RUN`: one run's report to attach to a ticket or send to a vendor (issue 614, SPEC C-24, ADR 0050).

Reads `report.json` and `report.md` of the run only; it never talks to the cluster. `--anonymize` replaces every production node name
with its lab name (`lab-node-NNN`, the order `lab_nodes.plan` uses: the sorted production names) and every production user name with
`<user>`. The user mapping itself is never written.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import lab, lab_import, lab_nodes, lab_run


class ExportError(Exception):
    pass


def folder(name: str, run: str, root: Path | None = None) -> Path:
    """The run's folder; one message for each condition."""
    if not lab.exists(name, root):
        raise ExportError(f"no lab {name}: import it first (benchctl import lab {name} --from DIR)")
    runs = sorted(p.name for p in lab_run.reports_dir(name, root).glob("*") if p.is_dir())
    if run == "last":
        if not runs:
            raise ExportError(f"lab {name} has no run yet: benchctl run lab {name}")
        run = runs[-1]
    f = lab_run.reports_dir(name, root) / run
    if "/" in run or not f.is_dir():
        raise ExportError(f"lab {name} has no run {run} (runs: {', '.join(runs) or 'none'})")
    if not (f / "report.json").is_file() or not (f / "report.md").is_file():
        raise ExportError(f"run {run} of lab {name} has no report (it was aborted or is still running)")
    return f


def node_map(name: str, root: Path | None = None) -> dict[str, str]:
    """Production node name -> lab name, over every node of the lab's slurm.conf (sorted, so the mapping is stable)."""
    b = lab.load_baseline(name, root)["slurm"] or {"nodes": []}
    names = sorted({n for line in b["nodes"] for n in lab_import.expand_hostlist(line["NodeName"])})
    return {n: lab_nodes.SERVICE.format(i) for i, n in enumerate(names, 1)}


def _replacer(mapping: dict[str, str]):
    if not mapping:
        return lambda text: text
    pat = re.compile(r"(?<![A-Za-z0-9_.-])(" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)) + r")(?![A-Za-z0-9_-])")
    return lambda text: pat.sub(lambda m: mapping[m.group(1)], text)


def _walk(value: Any, fn) -> Any:
    if isinstance(value, dict):
        return {fn(k): _walk(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, fn) for v in value]
    return fn(value) if isinstance(value, str) else value


def export(name: str, run: str, anonymize: bool = False, root: Path | None = None) -> tuple[dict, str]:
    """(the report document, its Markdown), anonymized when asked."""
    f = folder(name, run, root)
    doc, md = json.loads((f / "report.json").read_text()), (f / "report.md").read_text()
    if anonymize:
        nodes = _replacer(node_map(name, root))
        users = {u: "<user>" for u in lab_run.production_users(name, root)}
        users_sub = _replacer(users)

        def fn(text: str) -> str:
            return users_sub(nodes(text))
        doc, md = _walk(doc, fn), fn(md)
        doc["anonymized"] = True
    return doc, md
