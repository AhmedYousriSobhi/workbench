"""Agent output mode (SPEC AG-6, AG-8; ADR 0065, 0067).

Two separate switches, so a script and an agent each get what is safe for them:

- `plain()`: stdout is not a terminal, or agent mode is on. Output carries no colour, progress bar or spinner (it saves
  tokens and breaks no script).
- `agent()`: `BENCHCTL_AGENT=1` only. Errors with a code become one line, a long log goes to a file, and a destructive verb
  needs `--yes`. Cron jobs and scripts never set it, so they see no change.
"""
from __future__ import annotations

import os
import sys

ENV = "BENCHCTL_AGENT"


def agent() -> bool:
    return os.environ.get(ENV) == "1"


def plain() -> bool:
    return agent() or not sys.stdout.isatty()


def apply() -> None:
    """Tell the libraries that colour nothing when the output is plain (https://no-color.org)."""
    if plain():
        os.environ.setdefault("NO_COLOR", "1")
