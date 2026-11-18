"""`benchctl ssh-key install|remove`."""

import typer

from .. import stack
from .base import SuggestingGroup

ssh_key_app = typer.Typer(cls=SuggestingGroup, help="Put the login key where a client that only reads ~/.ssh can use it")


@ssh_key_app.command("install")
def ssh_key_install() -> None:
    """Copy the login key to ~/.ssh/workbench_ed25519 (mode 600) and print the path to give the client."""
    try:
        stack.install_client_key()
    except (OSError, FileNotFoundError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(f"key: ~/.ssh/{stack.CLIENT_KEY}  (host 127.0.0.1, port 2222, user alice)\nremove it when done: benchctl ssh-key remove")


@ssh_key_app.command("remove")
def ssh_key_remove() -> None:
    """Delete ~/.ssh/workbench_ed25519 (only if it is the workbench key)."""
    typer.echo("removed" if stack.remove_client_key() else "nothing to remove")
