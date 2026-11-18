"""Node health checks: `add|remove|validate|reload check(s)` (ADR 0019); `set check` is in adjust.py."""

from pathlib import Path as _Path

import typer

from .. import completion, nhc
from .base import CHECK, POSITION, SETS, YES, SuggestingGroup, _confirm, _nhc_done, _nhc_run
from .groups import add_app, remove_app, validate_app

reload_app = typer.Typer(cls=SuggestingGroup, help="Push edited files to the running nodes: the health checks")


@add_app.command("check")
def add_check(name: str = typer.Argument(..., help="new check name: lower case letters, digits, '-'"),
              template: str = typer.Option(None, "--template", autocompletion=completion.check_templates, help="a builtin template, e.g. metric-above"),
              script: str = typer.Option(None, "--script", help="a shell file with your own check (prints one line per problem)"),
              sets: list[str] = SETS, position: int = POSITION) -> None:
    """Add a health check. From a template: `benchctl add check gpu-hot --template metric-above --set metric=DCGM_FI_DEV_GPU_TEMP --set max=85`.
    Your own: `benchctl add check my-check --script ./my-check.sh` (the file prints one line per problem, nothing when healthy). The
    new check is validated first, including a run on healthy sample metrics, and refused when it fails them."""
    if template is None and script is None:
        typer.echo("give --template NAME (see the names with `ls images/node-base/nhc.d/templates`) or --script FILE", err=True)
        raise typer.Exit(2)
    if template is not None and script is not None:
        typer.echo("give --template or --script, not both", err=True)
        raise typer.Exit(2)

    def go() -> None:
        body = _Path(script).read_text() if script else None
        nhc.add(name, template=template, script=body, params=nhc.parse_sets(sets), position=position)

    try:
        _nhc_run(go)
    except OSError as e:
        typer.echo(f"cannot read {script}: {e.strerror}", err=True)
        raise typer.Exit(1) from e
    _nhc_done(f"added check {name}")


@remove_app.command("check")
def remove_check(name: str = CHECK, yes: bool = YES) -> None:
    """Remove a check and its file from images/node-base/nhc.d (git keeps the old one). To keep it, `set check NAME off` instead."""
    _confirm(f"This removes the check {name} and its file.", yes, ask=False)
    _nhc_run(nhc.remove, name)
    _nhc_done(f"removed check {name}")


@validate_app.command("checks")
def validate_checks() -> None:
    """Validate every check file and checks.conf (grammar, parameters, bash syntax, the contract, a run on healthy sample metrics). Changes nothing."""
    errs = nhc.problems()
    if errs:
        typer.echo("\n".join(errs), err=True)
        raise typer.Exit(1)
    typer.echo(f"ok: {len(nhc.load())} checks valid")


@reload_app.command("checks")
def reload_checks() -> None:
    """After editing a check file or images/node-base/nhc.d/checks.conf by hand: validate, then push to the running nodes. Nothing is pushed when a check is invalid."""
    _nhc_done("validated and delivered")
