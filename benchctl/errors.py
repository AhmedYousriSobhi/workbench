"""Stable error codes (SPEC AG-6, ADR 0065).

`WB-<KIND>-<DETAIL>`. `REGISTRY` is the one list: every code has a meaning and the fix command, and `tests/test_error_codes.py`
checks it against the code. A person sees the message as before; with `BENCHCTL_AGENT=1` the same condition is one line,
`CODE: message | fix: COMMAND`, on stderr. `python -m benchctl.errors` writes docs/agents/ERROR-CODES.md from this table.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import typer

from . import agentmode

# code -> (what it means, the fix: a command to run, or the step to take)
REGISTRY: dict[str, tuple[str, str]] = {
    "WB-REFUSED-MEMORY": ("Not enough free memory to start this; the governor's pre-flight said no.", "benchctl plan"),
    "WB-REFUSED-GOVERNOR": ("The governor admits no new work at its current memory level.", "benchctl status"),
    "WB-REFUSED-NETWORK": ("The cluster network cannot be created or does not match the addressing plan.", "benchctl down"),
    "WB-UNKNOWN-VERB": ("No such command.", "benchctl --help"),
    "WB-UNKNOWN-NODE": ("No such node.", "benchctl get nodes"),
    "WB-UNKNOWN-TARGET": ("No such node or service.", "benchctl get services"),
    "WB-UNKNOWN-PROFILE": ("No such size preset.", "benchctl get profiles"),
    "WB-UNKNOWN-SCENARIO": ("No such scenario.", "benchctl get scenarios"),
    "WB-PART-NOT-RUNNING": ("A part this needs is not running (Prometheus, or the web page).", "benchctl start"),
    "WB-STACK-NOT-RUNNING": ("No container of the workbench is running.", "benchctl start"),
    "WB-EXTRA-MISSING": ("An optional extra is not installed.", "pip install -e '.[mcp]'"),
    "WB-USAGE": ("The command line is wrong.", "benchctl VERB --help"),
    "WB-NEEDS-YES": ("A destructive command needs --yes in agent mode.", "repeat the command with --yes"),
    "WB-DOCTOR-CGROUP-V2": ("cgroup v2 is not the unified hierarchy.", "benchctl doctor"),
    "WB-DOCTOR-MEMORY-PSI": ("/proc/pressure/memory is missing.", "benchctl doctor"),
    "WB-DOCTOR-KERNEL": ("The kernel is older than 5.19 (no memory.reclaim).", "benchctl doctor"),
    "WB-DOCTOR-DOCKER": ("Docker is not reachable or not on the systemd cgroup driver.", "benchctl doctor"),
    "WB-DOCTOR-DOCKER-CGROUPS": ("Docker's cgroup driver or version is not supported.", "benchctl doctor"),
    "WB-DOCTOR-LIBVIRT": ("libvirt is not reachable (some profiles only).", "benchctl doctor"),
    "WB-DOCTOR-SWAP": ("No swap, or heavily used swap.", "benchctl doctor"),
    "WB-DOCTOR-MEMLOCK": ("The hard memlock limit is too low for the governor.", "benchctl doctor"),
    "WB-DOCTOR-TOOL-DOCKER": ("The docker tool is missing.", "benchctl doctor"),
    "WB-DOCTOR-TOOL-VIRSH": ("The virsh tool is missing (some profiles only).", "benchctl doctor"),
    "WB-DOCTOR-TOOL-VAGRANT": ("The vagrant tool is missing (some profiles only).", "benchctl doctor"),
    "WB-DOCTOR-TOOL-TC": ("The tc tool is missing (some profiles only).", "benchctl doctor"),
    "WB-DOCTOR-TSH": ("The local tsh is newer than the Teleport server.", "benchctl doctor"),
    "WB-DOCTOR-GOVERNOR": ("The governor is not reachable.", "benchctl start"),
    "WB-DOCTOR-OOMD-BACKSTOP": ("The optional systemd-oomd backstop is installed but not active.", "benchctl doctor"),
    "WB-DOCTOR-SIM-CONTAINERS": ("A simulation container is unhealthy.", "benchctl status"),
}
CODE = re.compile(r"^WB-[A-Z]+(-[A-Z0-9]+)*$")


def doctor_code(check_name: str) -> str:
    """One code per doctor check: its name without a version number ("tsh 17.0.1" is `WB-DOCTOR-TSH`)."""
    words = [w for w in re.split(r"[^A-Za-z0-9.]+", check_name) if w and not re.fullmatch(r"[0-9][0-9.]*", w)]
    return "WB-DOCTOR-" + "-".join(w.upper() for w in words)


def fix_for(code: str) -> str:
    return REGISTRY[code][1]


def render(code: str, detail: str, human: str | None = None, fix: str | None = None) -> str:
    """What to print for this condition: `human` (default `detail`) for a person, `CODE: detail | fix: ...` in agent mode."""
    if not agentmode.agent():
        return human if human is not None else detail
    return f"{code}: {' '.join(detail.split())} | fix: {fix or fix_for(code)}"


class CodedBadParameter(typer.BadParameter):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def unknown_code(what: str) -> str:
    return {"node": "WB-UNKNOWN-NODE", "node or service": "WB-UNKNOWN-TARGET", "profile": "WB-UNKNOWN-PROFILE",
            "scenario": "WB-UNKNOWN-SCENARIO"}.get(what, "WB-USAGE")


def click_line(e) -> str:
    """The one-line form of a usage error (agent mode)."""
    text = getattr(e, "message", None) or e.format_message()
    # typer rebuilds the "no such command" error as its own type, so the code is read from the text
    code = getattr(e, "code", None) or ("WB-UNKNOWN-VERB" if text.startswith("No such command") else "WB-USAGE")
    return render(code, text)


def needs_yes(what: str) -> None:
    sys.stderr.write(render("WB-NEEDS-YES", what, fix="repeat the command with --yes") + "\n")


def table() -> str:
    intro = ("With `BENCHCTL_AGENT=1` an error is one line on stderr: `CODE: message | fix: COMMAND`. "
             "The codes are stable (SPEC AG-6, ADR 0065); a new one is added to `benchctl/errors.py`, never renamed. "
             "Generated by `python -m benchctl.errors`.")
    rows = ["# Error codes", "", intro, "", "| Code | Meaning | Fix |", "|---|---|---|"]
    rows += [f"| `{c}` | {m} | `{f}` |" for c, (m, f) in sorted(REGISTRY.items())]
    return "\n".join(rows) + "\n"


if __name__ == "__main__":
    Path("docs/agents/ERROR-CODES.md").write_text(table())
