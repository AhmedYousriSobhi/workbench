"""AGENTS.md generated blocks (SPEC AG-1, ADR 0060, 0068): `python -m benchctl.agents` rewrites them, `--check` fails when stale.

AGENTS.md is the one short file an agent reads to use workbench as a test target. Its endpoint table, verb list and contract
version are generated from the code between `<!-- BEGIN name -->` and `<!-- END name -->`, so it cannot drift; a test runs the
check. The rest of the file is hand-written and kept under the 2,000 token budget (characters / 4).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from . import contract

AGENTS = Path("AGENTS.md")
MAX_TOKENS = 2000
# the ten verbs that matter, as the command path; the one-line meaning is the command's own help text
VERBS = ["start", "stop", "plan", "describe workbench", "get faults", "get endpoints", "get traffic", "scenario run", "wait ready", "export profile"]
SAYS = {"start": "Check the machine, build, start the cluster and the web page", "get endpoints": "Where a client connects, and which file holds each credential"}  # where the command's own help is not a one-liner
CORE_ENDPOINTS = ("login", "nodes", "bmc", "bcm", "jira", "slurm", "gpu")  # the others are optional parts: `get endpoints` lists them


def tokens(text: str) -> int:
    return -(-len(text) // 4)


def command_at(path: str):
    """The Typer/Click command for a path such as "scenario run", or None."""
    import typer.main

    from . import cli

    cmd = typer.main.get_command(cli.app)
    for word in path.split():
        cmd = getattr(cmd, "commands", {}).get(word)
        if cmd is None:
            return None
    return cmd


def first_sentence(text: str | None) -> str:
    text = " ".join((text or "").split())
    return re.split(r"(?<=[.;:])\s", text, maxsplit=1)[0].rstrip(".;:")


def verbs_block() -> str:
    rows = ["| Command | What it does |", "|---|---|"]
    for v in VERBS:
        cmd = command_at(v)
        rows.append(f"| `benchctl {v}` | {SAYS.get(v) or first_sentence(cmd.help if cmd else 'MISSING')} |")
    return "\n".join(rows)


def endpoints_block() -> str:
    from webui import connections as conn

    from . import cli

    rows = ["| Id | Address | Credential (a file, never printed) |", "|---|---|---|"]
    for e in conn.entities(cli._base_sim()):
        if e["id"] in CORE_ENDPOINTS:
            rows.append(f"| {e['id']} | `{e['address']}` | {('`' + e['credential'] + '`') if e.get('credential') else 'none'} |")
    return "\n".join(rows)


def contract_block() -> str:
    return f"JSON contract version {contract.CONTRACT_VERSION}. Schemas: `docs/agents/schemas/`. Changes: `docs/agents/CHANGELOG.md`. Error codes: `docs/agents/ERROR-CODES.md`."


BLOCKS = {"endpoints": endpoints_block, "verbs": verbs_block, "contract": contract_block}


def render(text: str) -> str:
    for name, fn in BLOCKS.items():
        pat = re.compile(rf"(<!-- BEGIN {name} -->\n)(?:.*?\n)?(<!-- END {name} -->)", re.DOTALL)
        if not pat.search(text):
            raise SystemExit(f"AGENTS.md has no <!-- BEGIN {name} --> ... <!-- END {name} --> block")
        text = pat.sub(lambda m, fn=fn: m.group(1) + fn() + "\n" + m.group(2), text)
    return text


def main(argv: list[str]) -> int:
    old = AGENTS.read_text()
    new = render(old)
    if "--check" in argv:
        if new != old:
            print("AGENTS.md is stale: run python -m benchctl.agents", file=sys.stderr)
            return 1
        return 0
    AGENTS.write_text(new)
    print(f"AGENTS.md: {tokens(new)} tokens of {MAX_TOKENS}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
