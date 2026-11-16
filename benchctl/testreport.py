"""`benchctl export test-report`: a report of the page's last scenario run, for a bug report (issue 739, ADR 0064 addendum).

A run is what the web page's `Runs` holds (played on the page, or posted by `benchctl scenario run`, ADR 0086). `build()` is the Python
twin of `buildReport()` in webui/static/js/testrunmodel.js and gives the same data and Markdown (tests/test_testreport.py compares them
through Node); `report()` adds what only the CLI can read, labelled "at export", and applies the privacy rules: no request bodies, no
header values, no tokens, hostnames or paths, no step `do` (only its words), no capture contents.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime

from . import testwords

HOST_CLI = "needs the host CLI"


class NoRun(Exception):
    """There is nothing to export for this request: one sentence that names the recovery step."""


def n(v) -> str:
    """A number as JavaScript prints it (12.0 is "12")."""
    return testwords.js(v)


def cell(v) -> str:
    return "n/a" if v is None else n(v)


def iso(secs: float) -> str:
    return datetime.fromtimestamp(int(secs // 1), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ran_at(t) -> str:
    return "did not run" if t is None else f"{n(t)} s"


def to_fixed0(x) -> str:
    return str(int(x + 0.5))  # JavaScript's toFixed(0) rounds a half up; Python's round() goes to the even number


def verdict_of(run: dict) -> str | None:
    if not run or not run.get("name"):
        return None
    if run.get("running"):
        return "running"
    if run.get("stopped"):
        return "stopped"
    skipped = any(x.get("status") == "skipped" for x in run.get("steps") or []) or any(x.get("status") == "skipped" for x in run.get("expects") or [])
    return "failed" if not run.get("ok") else "passed with skips" if skipped else "passed"


def plain(x: dict) -> str:
    if not x.get("detail"):
        return ""
    if "unreachable" in x["detail"]:
        return f"can't reach {n(x.get('target'))}: is the stack running?"
    if "Slurm steps need" in x["detail"]:
        return HOST_CLI
    return x["detail"]


def timeline_rows(run: dict, say: Callable[[str], str] = str, raw_do: bool = True) -> list[dict]:
    """One row per step of the run (its state holds the plan and the results together). `say` cleans the free text of a result."""
    rows = []
    for p in run.get("steps") or []:
        page = p.get("page") is not False
        why = "" if page else (p.get("why") or HOST_CLI)
        rows.append({"at": p["at"], "text": testwords.describe(p, raw_do), "target": p["target"], "page": page, "why": why,
                     "status": p.get("status", "pending"), "note": say(plain(p)) if p.get("detail") else "", "t": p.get("t")})
    return rows


def eye_rows(snap: dict | None, base: dict | None) -> list[dict]:
    """Per service the counters the mock keeps: `snap` at the end of the run, `base` at Play (same as the page's eyeRows)."""
    if not snap or not snap.get("reachable"):
        return []
    totals, errors = snap.get("totals"), snap.get("errors")
    rows = []
    for k in sorted(snap["reachable"]):
        measured = totals is not None and k in totals
        tot = totals[k] if measured else None
        up = bool(snap["reachable"][k])
        b = base if base and base.get("totals") is not None and k in base["totals"] else None
        during = max(0, tot - b["totals"][k]) if measured and b else None
        err_during = (max(0, errors[k] - b["errors"][k])
                      if measured and b and errors is not None and k in errors and b.get("errors") is not None and k in b["errors"] else None)
        called = (during if b else tot) > 0 if measured else False
        verdict = "not reachable" if not up else "not measured" if not measured else "called" if called else "not called yet"
        rows.append({"key": k, "up": up, "total": tot, "during": during, "errors": err_during, "verdict": verdict})
    return rows


def build(run: dict, snap: dict | None, base: dict | None, say: Callable[[str], str] = str, raw_do: bool = True) -> tuple[dict, str]:
    """(data, markdown) of one run: the same as the page's report (`buildReport`), from the run's own state. `say` cleans the free text
    a command or a mock wrote (a step's note, an expectation's detail), `raw_do` whether a step with no words may show its raw action
    (the page does); the defaults keep both as the page does."""
    rows, expects, verdict = timeline_rows(run, say, raw_do), run.get("expects") or [], verdict_of(run) or "not run"
    t0 = run.get("started") if isinstance(run.get("started"), (int, float)) and not isinstance(run.get("started"), bool) else None
    ended = not run.get("running") and run.get("elapsed") is not None
    eye = eye_rows(snap, base)
    data = {
        "scenario": run.get("name"), "ref": run.get("ref") or "", "speed": run.get("speed") or 1, "verdict": verdict,
        "ran_from": "cli" if run.get("source") == "cli" else "page",
        "started": iso(t0) if t0 is not None else None, "finished": iso(t0 + run["elapsed"]) if t0 is not None and ended else None,
        "duration_s": run.get("elapsed"),
        "steps": [{"at": r["at"], "t": r["t"], "target": r["target"], "action": r["text"], "status": r["status"], "page_could_run": r["page"],
                   "note": r["note"] or r["why"]} for r in rows],
        "expectations": [{"label": x.get("label"), "words": testwords.expect_words(x.get("label")), "status": x.get("status"),
                          "t": x.get("t"), "detail": say(x.get("detail") or "")} for x in expects],
        "clients_eye": [{"service": e["key"], "calls_during_run": e["during"], "errors_during_run": e["errors"], "verdict": e["verdict"]} for e in eye],
    }
    cli = data["ran_from"] == "cli"
    ref = f" ({data['ref']})" if data["ref"] else ""
    took = "n/a" if data["duration_s"] is None else f"{n(data['duration_s'])} s"
    started = data["started"] or "not captured (the run began before this page was opened)"
    md = [f"# Test run: {data['scenario']}{ref}", "",
          f"- Result: **{verdict}**", f"- Ran from: {'the CLI (benchctl scenario run)' if cli else 'the web page'}", f"- Speed: {n(data['speed'])}×",
          f"- Started: {started}", f"- Finished: {data['finished'] or 'n/a'}", f"- Took: {took}", "",
          "## What was injected", "", "At: planned, seconds into the scenario. Ran at: seconds after Play, at this speed.", "",
          "| At | Ran at | Target | Action | Result |", "|---|---|---|---|---|"]
    for s in data["steps"]:
        note = f": {s['note'].replace('|', '/')}" if s["note"] else ""
        md.append(f"| {to_fixed0(s['at'])} s | {ran_at(s['t'])} | {s['target']} | {s['action'].replace('|', '/')} | {s['status']}{note} |")
    md += ["", "## Expectations", ""]
    for x in data["expectations"]:
        at = f" at {n(x['t'])} s" if x["t"] is not None else ""
        detail = f" ({x['detail']})" if x["detail"] else ""
        md.append(f"- {x['status']}{at}: {x['words']}{detail}")
    if not data["expectations"]:
        md.append("- none declared")
    md += ["", "## What the client called (mock counters during the run)", "", "| Service | Calls | Errors | Verdict |", "|---|---|---|---|"]
    md += [f"| {e['service']} | {cell(e['calls_during_run'])} | {cell(e['errors_during_run'])} | {e['verdict']} |" for e in data["clients_eye"]]
    md += ["", "Skipped steps were skipped by the CLI; they are not passes. n/a means the mock keeps no counter for it." if cli
           else "Skipped steps were not run by the page (they need the host CLI); they are not passes. n/a means the mock keeps no counter for it.", ""]
    return data, "\n".join(md)


# ---- what the CLI adds, and what it leaves out ----
# A step's or an expectation's detail is text a command or a mock wrote. The page's report shows it as it is; this one keeps the words and
# drops what a bug report must not carry: a path, an address, a credential.
SCRUB = [(re.compile(r"(?i)\b(bearer|basic)\s+[\w.~+/=-]{6,}"), r"\1 <redacted>"),
         (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"), "<redacted>"),
         (re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization)(\s*[=:]\s*)\S+"), r"\1\2<redacted>"),
         (re.compile(r"(?<![\w.])(?:~|/(?:home|root|tmp|var|etc|run|usr|opt|srv|mnt|proc|sys|compose|Users)\b)[^\s'\"),;]*"), "<path>"),
         (re.compile(r"(?<![\w.])/(?:[\w.@+-]+/)+[\w.@+-]+"), "<path>"),
         (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<address>"),
         (re.compile(r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:local|internal|lan|corp|com|net|org|io)\b(?::\d+)?"), "<host>")]


def scrub(text: str | None) -> str:
    out = text or ""
    for pat, rep in SCRUB:
        out = pat.sub(rep, out)
    return out


def _counts(rows: list[dict] | None) -> dict | None:
    if rows is None:
        return None
    out: dict[str, int] = {}
    for r in rows:
        out[r["state"]] = out.get(r["state"], 0) + 1
    return dict(sorted(out.items()))


def at_export(taken: float, nodes: list[dict] | None, faults: list[dict] | None, alerts: list[dict] | None) -> dict:
    """What is true when the report is made, not when the run ended: node container states (a count per state), what is wrong now
    (`get faults`), and the alerts Prometheus is pending or firing (name, state, severity: never their labels, which hold addresses).
    None means it could not be read; it never reads as an empty list."""
    return {"taken": iso(taken), "nodes": _counts(nodes),
            "faults": None if faults is None else [{k: scrub(str(r.get(k, ""))) for k in ("target", "kind", "state", "label")} for r in faults],
            "alerts": None if alerts is None else [{"name": a.get("name", "-"), "state": a.get("state", "-"), "severity": a.get("severity", "-")} for a in alerts]}


def at_export_markdown(x: dict) -> str:
    nodes = "not read (Docker does not answer)" if x["nodes"] is None else ", ".join(f"{c} {s}" for s, c in x["nodes"].items()) or "none defined"
    lines = ["## At export (when this report was made, not when the run ended)", "", f"- Taken: {x['taken']}", f"- Nodes (container state): {nodes}"]
    if x["faults"] is None:
        lines.append("- Faults now: not read")
    else:
        lines.append("- Faults now: " + ("none" if not x["faults"] else ""))
        lines += [f"  - {f['target']} ({f['kind']}): {f['state']}, {f['label']}" for f in x["faults"]]
    if x["alerts"] is None:
        lines.append("- Alerts: not read (Prometheus is not running)")
    else:
        lines.append("- Alerts: " + ("none pending or firing" if not x["alerts"] else ""))
        lines += [f"  - {a['name']} ({a['severity']}): {a['state']}" for a in x["alerts"]]
    return "\n".join(lines) + "\n"


def pick(state: dict, want: str) -> dict:
    """The page's run state for `want` ("last", or the scenario's name or reference), or the reason there is nothing to export."""
    if not state.get("name"):
        raise NoRun("nothing to export: the page has no scenario run (it keeps one only while it is up). Play one with the page up "
                    "(`benchctl open`, or `benchctl scenario run NAME`), then export it")
    if want != "last" and want.lower() not in (str(state["name"]).lower(), str(state.get("ref") or "").lower()):
        raise NoRun(f"nothing to export for {want!r}: the page's last run is {state['name']!r}. Export it with `benchctl export test-report last`, or play {want!r} again")
    if state.get("running"):
        raise NoRun(f"{state['name']} is still running: wait for it with `benchctl wait scenario {state['name']}`, then export it")
    return state


def report(state: dict, want: str, taken: float, nodes: Callable, faults: Callable, alerts: Callable) -> tuple[dict, str]:
    """(data, markdown) for the run in `state`. `nodes`, `faults` and `alerts` read the cluster now; each returns None when it cannot."""
    run = pick(state, want)
    counters = run.get("counters") or {}
    data, md = build(run, counters.get("end"), counters.get("base"), scrub, raw_do=False)
    data["at_export"] = at_export(taken, nodes(), faults(), alerts())
    return data, md.rstrip("\n") + "\n\n" + at_export_markdown(data["at_export"])
