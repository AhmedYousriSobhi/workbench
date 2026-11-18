"""`benchctl completion install|remove|<shell>`."""

from pathlib import Path as _Path

import typer

from .. import completion
from .base import SuggestingGroup

completion_app = typer.Typer(cls=SuggestingGroup, help="Tab completion: install, remove, or print the script for bash, zsh, fish")


def _completion_install(shell: str) -> None:
    for line in completion.install(shell, _Path(__file__).resolve().parents[2]):
        typer.echo(line)


def _shell_arg(shell: str | None) -> str:
    shell = shell or completion.detect_shell()
    if shell not in completion.SHELLS:
        typer.echo(f"which shell? Valid: {', '.join(completion.SHELLS)} (e.g. benchctl completion install bash)", err=True)
        raise typer.Exit(2)
    return shell


SHELL = typer.Argument(None, help="bash, zsh or fish (default: the one in $SHELL)")


@completion_app.command("install")
def completion_install(shell: str = SHELL) -> None:
    """Turn on Tab completion for your shell. No sudo; one line in your rc file; undo with `completion remove`."""
    _completion_install(_shell_arg(shell))


@completion_app.command("remove")
def completion_remove(shell: str = SHELL) -> None:
    """Undo `completion install`: the script, the rc line and the ~/.local/bin link."""
    for line in completion.remove(_shell_arg(shell), _Path(__file__).resolve().parents[2]):
        typer.echo(line)


for _sh in completion.SHELLS:
    def _show(_sh: str = _sh) -> None:
        typer.echo(completion.script(_sh), nl=False)
    _show.__doc__ = f"Print the {_sh} completion script (source it yourself, or use `completion install`)."
    completion_app.command(_sh)(_show)
