"""The root `app` and the command groups that several modules add verbs to.

`commands/__init__.py` imports the modules in the order `--help` lists the verbs. `cli.py` mounts the groups.
"""

import typer

from .base import SuggestingGroup

app = typer.Typer(cls=SuggestingGroup, add_completion=False, epilog="Scripts: use -o json where offered, read data from stdout (messages and errors "
                  "go to stderr). Exit codes: 0 ok, 1 failed or refused, 2 bad usage.", help="benchctl: control the simulated cluster. Pattern: benchctl VERB [RESOURCE] [NAME]. "
                       "Start with `benchctl start`, look with `benchctl get nodes`, stop with `benchctl stop`.")

add_app = typer.Typer(cls=SuggestingGroup, help="Add something: a health check, a Teleport invite")

remove_app = typer.Typer(cls=SuggestingGroup, help="Remove something: a health check, the simulated users' jobs")

validate_app = typer.Typer(cls=SuggestingGroup, help="Check files without changing anything: the health checks, a config lab")

run_app = typer.Typer(cls=SuggestingGroup, help="Run something: a config lab's replay, ticket automation against the mock or the real Jira")
