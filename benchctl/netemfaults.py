"""In-cluster network faults (issue 478, ADR 0056, SPEC C-12): the `netem` scenario target and `benchctl set|clear netem`.

Nothing here runs `tc`. Each call asks the governor over its socket; only the optional root service answers (the user-mode governor
refuses with a message that names the install script). The page never offers it: the target is a host-CLI target and the page's
scenario player skips it (W-3). Bounds and parsing live in `governor/netem.py`, so the CLI, scenario validation and the governor agree.

A scenario step: `{target: netem, do: {nodes: [cn001], delay: 200ms, loss: 5%, ttl: 60s}}` or `{target: netem, do: {clear: true}}`
(optionally with `nodes:`). Every set has a TTL (60 s when omitted) and ends by itself.
"""
from __future__ import annotations

import os
from collections.abc import Callable

from governor import netem

from . import checks

KEYS = ("nodes", "delay", "loss", "ttl", "clear")


def _values(do: dict) -> tuple[int | None, float | None, int | None, list[str]]:
    """(delay_ms, loss_pct, ttl_s, messages): a value that does not parse is one message, not a silent default."""
    msgs, delay, loss, ttl = [], None, None, None
    if "delay" in do and (delay := netem.parse_delay(do["delay"])) is None:
        msgs.append(f"netem delay {do['delay']!r} must look like 200ms (1..{netem.MAX_DELAY_MS} ms)")
    if "loss" in do and (loss := netem.parse_loss(do["loss"])) is None:
        msgs.append(f"netem loss {do['loss']!r} must look like 5% (0.1..{netem.MAX_LOSS_PCT:g} %)")
    if "ttl" in do and (ttl := netem.parse_ttl(do["ttl"])) is None:
        msgs.append(f"netem ttl {do['ttl']!r} must look like 60s or 5m ({netem.MIN_TTL_S}..{netem.MAX_TTL_S} s)")
    return delay, loss, ttl, msgs


def problems(do: dict, nodes: set[str]) -> list[str]:
    """Scenario validation without touching the stack: one message per condition."""
    out = [f"unknown netem key {k!r} (one of {', '.join(KEYS)})" for k in do if k not in KEYS]
    names = do.get("nodes")
    if names is not None and (not isinstance(names, list) or not names):
        out.append("netem nodes must be a list of node names")
        names = []
    out += [f"unknown node {n!r}: netem targets this cluster's Slurm nodes only" for n in names or [] if n not in nodes]
    if do.get("clear") is not None and not isinstance(do["clear"], bool):
        out.append("netem clear is true or false")
    if do.get("clear") is True:
        out += [f"netem clear takes no {k} (it only removes rules)" for k in ("delay", "loss", "ttl") if k in do]
        return out
    if not names:
        out.append("netem needs nodes: [cn001, ...] (or clear: true)")
    delay, loss, ttl, msgs = _values(do)
    out += msgs
    if not msgs:
        out += netem.problems(delay, loss, ttl)
    return out


def _ask(req: dict, ask: Callable[[dict], dict] | None) -> dict:
    sock = os.environ.get("WORKBENCH_GOV_SOCKET", checks.GOV_SOCKET)
    try:
        r = (ask or (lambda q: checks.governor_request(q, sock, timeout=60)))(req)
    except OSError as e:
        return {"ok": False, "error": f"governor not reachable ({e}); netem needs the root governor service: {netem.INSTALL_HINT}"}
    if not r.get("ok") and str(r.get("error", "")).startswith("unknown command"):
        return {"ok": False, "error": f"this governor has no netem; reinstall the root service: {netem.INSTALL_HINT}"}
    return r


def apply(do: dict, ask: Callable[[dict], dict] | None = None) -> str:
    """Run one step; "" when it worked, else the one message that says why not."""
    if do.get("clear") is True:
        names = do.get("nodes") or [None]
        for n in names:
            r = _ask({"cmd": "netem-clear", **({"node": n} if n else {})}, ask)
            if not r.get("ok"):
                return str(r.get("error"))
        return ""
    delay, loss, ttl, msgs = _values(do)
    if msgs or (msgs := netem.problems(delay, loss, ttl)):
        return "; ".join(msgs)
    for n in do.get("nodes") or []:
        req = {"cmd": "netem-set", "node": n, "ttl_s": ttl or netem.DEFAULT_TTL_S}
        if delay is not None:
            req["delay_ms"] = delay
        if loss is not None:
            req["loss_pct"] = loss
        r = _ask(req, ask)
        if not r.get("ok"):
            return str(r.get("error"))
    return ""
