"""What exists, as `benchctl get <resource>` prints it: nodes, services, profiles, scenarios."""
import json
from collections.abc import Callable
from pathlib import Path

from . import compose, nodes, scenario, stack

RESOURCES = ["nodes", "services", "profiles", "scenarios"]


def _containers(runner: Callable = stack._run) -> dict[str, str]:
    """service name -> Docker state of this project's containers (running, paused, exited...)."""
    r = runner(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={stack.PROJECT}",
                "--format", '{{.Label "com.docker.compose.service"}} {{.State}}'])
    return dict(line.split(None, 1) for line in r.stdout.splitlines() if " " in line)


def node_rows(sim: dict, runner: Callable = stack._run) -> list[dict]:
    """Every node `sim.yaml` defines, with its container state ('absent' when not started)."""
    live = _containers(runner)
    return [{"name": n, "kind": kind, "state": live.get(n, "absent")} for n, kind in nodes.node_names(sim)]


def service_rows(runner: Callable = stack._run) -> list[dict]:
    return [{"name": n, "state": s} for n, s in sorted(_containers(runner).items())]


def profile_rows() -> list[dict]:
    return [{"name": p.stem, "compute_nodes": compose.load_profile(p.stem).get("compute_nodes", "-"),
             "groups": ",".join(compose.load_profile(p.stem).get("groups", []))}
            for p in sorted(Path("profiles").glob("*.yaml"))]


def scenario_rows() -> list[dict]:
    return [{"name": s["name"], "steps": len(s.get("steps", [])), "description": s.get("description", "")}
            for s in scenario.list_all()]


class QueryError(ValueError):
    pass


def select(rows: list[dict], selectors: list[str]) -> list[dict]:
    """Keep rows matching every `key=value` or `key!=value` (a selector may list several, comma separated)."""
    wanted = [s.strip() for sel in selectors for s in sel.split(",") if s.strip()]
    for s in wanted:
        key, op, value = s.partition("!=") if "!=" in s else s.partition("=")
        if not op or not key or (rows and key not in rows[0]):
            cols = ", ".join(rows[0]) if rows else "none to check"
            raise QueryError(f"bad selector {s!r}: use key=value or key!=value (columns: {cols})")
        rows = [r for r in rows if (str(r[key]) == value) == (op == "=")]
    return rows


def sort_rows(rows: list[dict], key: str | None) -> list[dict]:
    if not key:
        return rows
    if rows and key not in rows[0]:
        raise QueryError(f"cannot sort by {key!r} (columns: {', '.join(rows[0])})")
    return sorted(rows, key=lambda r: (isinstance(r[key], str), r[key]))


def render(rows: list[dict], output: str = "table") -> str:
    """A table (default), JSON for scripts (`-o json`), or just the names, one per line (`-o name`)."""
    if output == "json":
        return json.dumps(rows, indent=2)
    if output == "name":
        return "\n".join(str(r["name"]) for r in rows)
    if output != "table":
        raise QueryError(f"unknown output {output!r}: use table, json or name")
    if not rows:
        return "none"
    cols = list(rows[0])
    width = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    lines = ["  ".join(c.upper().ljust(width[c]) for c in cols).rstrip()]
    lines += ["  ".join(str(r[c]).ljust(width[c]) for c in cols).rstrip() for r in rows]
    return "\n".join(lines)
