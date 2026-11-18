"""benchctl's command groups, one module each (issue 857). `cli.py` builds `app` and mounts them in the order `--help` shows.

Shared pieces (`SuggestingGroup`, `OUTPUT`, `JSON`, `_sim`, the node arguments, `_confirm`) live in `base.py`; `cli.py` imports some
back by name. A group that several modules add verbs to (`add`, `remove`, `validate`, `run`) is made in `groups.py`.

ORDER MATTERS: Typer lists a group's verbs in the order they were registered, which is the order these modules are imported. The
order below keeps `benchctl --help` and every group's `--help` as they were: `reset` (remove jobs, teleport-state, jira-real-state,
storage-state) before `nhc` (add/remove/validate/reload check), `nhc` before `invite` (add invite), `invite` before `lab`
(add candidate, remove lab, validate lab, run lab). `tests/test_cli_order.py` pins it.
"""

# ruff: noqa: I001  (a sorted import block would register the verbs in the wrong order)

from . import groups
from . import health, build, lifecycle, nodeops
from . import get, adjust, reset, nhc, invite, lab, describe, wait, export
from . import jira_real, load, measure, scenario, serve, shellcompletion, sshkey, tickets

__all__ = ["adjust", "build", "describe", "export", "get", "groups", "health", "invite", "jira_real", "lab", "lifecycle", "load", "measure",
           "nhc", "nodeops", "reset", "scenario", "serve", "shellcompletion", "sshkey", "tickets", "wait"]
