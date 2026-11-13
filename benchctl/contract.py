"""The JSON contract for scripts and agents (SPEC AG-3, ADR 0062).

Every JSON output carries `contract_version` (MAJOR.MINOR). A list becomes {"contract_version", "items": [...]}; an object
gets the field first. Schemas live in docs/agents/schemas/ (JSON Schema draft 2020-12, one per output) and every change is
a line in docs/agents/CHANGELOG.md: adding a field is a minor bump; removing or renaming one, or changing a type, is major.
No output may hold a secret value, only a pointer to one (a file path, or the command that prints it).
"""
from __future__ import annotations

import json

CONTRACT_VERSION = "1.3"


def wrap(data):
    """A list becomes the `items` of an object; an object keeps its keys. Either way `contract_version` comes first."""
    body = {"items": data} if isinstance(data, list) else dict(data)
    return {"contract_version": CONTRACT_VERSION, **body}


def dumps(data, compact: bool = False) -> str:
    """`compact` drops the indentation (describe workbench keeps to a size budget, SPEC AG-2)."""
    return json.dumps(wrap(data), separators=(",", ":")) if compact else json.dumps(wrap(data), indent=2)


def wanted(output: str, json_flag: bool = False) -> str:
    """`--json` is an alias of `-o json`."""
    return "json" if json_flag else output
