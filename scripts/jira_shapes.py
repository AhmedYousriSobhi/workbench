"""Shapes of Jira responses: keys and value types, never values (issue 229).

A recorded response is reduced to its shape before it is written, so a fixture holds no site name, account id, email, summary or
comment text. `compare` says where a mock's response lacks something the real service returned.
"""
from __future__ import annotations

SCALARS = {str: "string", bool: "boolean", int: "number", float: "number", type(None): "null"}


def shape(value):
    """dict -> {key: shape}; list -> [merged item shape] or []; scalar -> its type name. Strings are never kept."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [merge([shape(v) for v in value])] if value else []
    return SCALARS.get(type(value), "string")


def merge(shapes: list):
    """One shape for many: union of dict keys, union of scalar types ('a|b'); a key seen only sometimes may be null."""
    if len(shapes) == 1:
        return shapes[0]
    if all(isinstance(s, str) for s in shapes):
        return "|".join(sorted({t for s in shapes for t in s.split("|")}))
    present = [s for s in shapes if s != "null"]
    if len(present) < len(shapes) and present and all(isinstance(s, (dict, list)) for s in present):
        return _optional(merge(present))  # an object or list that is null in some items
    dicts = [s for s in present if isinstance(s, dict)]
    if dicts and len(dicts) == len(present):
        keys = {k for s in dicts for k in s}
        return {k: merge([s[k] for s in dicts if k in s]) if all(k in s for s in dicts) else _optional(merge([s[k] for s in dicts if k in s]))
                for k in sorted(keys)}
    lists = [s for s in present if isinstance(s, list)]
    if lists and len(lists) == len(present):
        items = [i for s in lists for i in s]
        return [merge(items)] if items else []
    return "mixed"


def _optional(s):
    """A key that is missing in some items: in a comparison it may be absent, so it is only type-checked when present."""
    return {"__optional__": s}


def _types(s) -> set[str]:
    return set(s.split("|")) if isinstance(s, str) else set()


def compare(real, mock, path: str = "") -> list[str]:
    """Problems where `mock` does not cover `real`. Extra keys in the mock are fine; null in `real` accepts any value."""
    if isinstance(real, dict) and set(real) == {"__optional__"}:
        return compare(real["__optional__"], mock, path) if mock is not None else []
    if isinstance(real, dict):
        if not isinstance(mock, dict):
            return [f"{path or '/'}: real has an object, mock has {_name(mock)}"]
        out: list[str] = []
        for key, sub in real.items():
            here = f"{path}/{key}"
            if key not in mock:
                if not (isinstance(sub, dict) and set(sub) == {"__optional__"}):
                    out.append(f"{here}: missing in mock")
                continue
            out += compare(sub, mock[key], here)
        return out
    if isinstance(real, list):
        if not isinstance(mock, list):
            return [f"{path or '/'}: real has a list, mock has {_name(mock)}"]
        if not real or not mock:
            return []  # nothing to compare against: an empty list in either one says nothing about the item shape
        return compare(real[0], mock[0], f"{path}[]")
    real_types = _types(real)
    if "null" in real_types or "mixed" in real_types:
        return []
    mock_type = _name(mock)
    return [] if mock_type in real_types else [f"{path or '/'}: real has {'|'.join(sorted(real_types))}, mock has {mock_type}"]


def _name(value) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "list"
    return SCALARS.get(type(value), "string")
