"""Three verbs an agent needs that had no CLI form (issue 635, ADR 0064 addendum): the state of the page's scenario run, one
call that clears every injected node fault, and a bounded one-shot form of `get capture`.

Each is the same code path a person's command or the web page uses; nothing here adds a privilege. The functions take their
effects as arguments so they are tested without Docker or a running page.
"""
from __future__ import annotations

from collections.abc import Callable

from . import capture, nodes, scenario

CAPTURE_ONCE_SECONDS, CAPTURE_ONCE_MAX_SECONDS = 10, 60
CAPTURE_ONCE_MESSAGES, CAPTURE_ONCE_MAX_MESSAGES = 200, 2000


def scenario_status(state: dict) -> dict:
    """The web page's run state as one small object (`/api/scenarios/run`): nothing run yet is `running: false, name: null`.
    A step's `do` is left out (the scenario file has it); each step keeps its time, target, status and detail."""
    steps = state.get("steps") or []
    return {"running": bool(state.get("running")), "name": state.get("name"), "speed": state.get("speed"), "ok": state.get("ok"),
            "stopped": bool(state.get("stopped")), "steps_total": len(steps), "steps_done": sum(1 for s in steps if s.get("status") != "pending"),
            "steps": [{k: s.get(k) for k in ("at", "target", "status", "detail")} for s in steps], "expects": list(state.get("expects") or [])}


def clear_faults(sim: dict, poster: Callable, runner: Callable) -> dict:
    """The page's Clear faults for every node at once: the BMC farm (temperature, fans, power, so the BCM, Slurm and the metrics follow)
    and the GPU exporter (XID, InfiniBand, NIC; GPU nodes only). Service and credential faults (`get faults` rows of kind service or
    auth) have their own restore steps in the scenario that made them and are not touched."""
    hardware = nodes.hardware_nodes(sim)
    plan = [("bmc-farm", [n for n, _ in hardware]), ("gpu-exporter", [n for n, k in hardware if k == "gpu"])]
    results = []
    for target, names in plan:
        if not names:
            continue
        o = scenario.execute(scenario.Step(0, target, {"nodes": names, "clear": True}), poster=poster, host=True, runner=runner)
        results.append({"target": target, "nodes": names, "status": o.status, "detail": o.detail})
    return {"ok": all(r["status"] != "failed" for r in results), "cleared": results}


def capture_once(sim: dict, node: str, seconds: int, max_messages: int, run: Callable | None = None) -> dict:
    """Messages on one node's wire for at most `seconds` or `max_messages`, whichever comes first, as one object (same message shape as
    the stream; nothing from a message body, nothing written to disk). `truncated` says the message limit ended it."""
    messages: list[dict] = []
    (run or capture.run)(sim, node, messages.append, seconds, stop=lambda: len(messages) >= max_messages)
    return {"node": node, "seconds": seconds, "count": len(messages), "truncated": len(messages) >= max_messages, "messages": messages[:max_messages]}
