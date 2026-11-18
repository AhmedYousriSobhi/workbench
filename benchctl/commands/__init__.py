"""benchctl's command groups, one module each (issue 857). `cli.py` builds `app` and mounts them in the order `--help` shows.

Shared pieces (`SuggestingGroup`, `OUTPUT`, `JSON`, `_sim`) live in `base.py`; `cli.py` imports them back by name.
"""

from . import jira_real, load, measure, scenario, serve, shellcompletion, sshkey, tickets

__all__ = ["jira_real", "load", "measure", "scenario", "serve", "shellcompletion", "sshkey", "tickets"]
