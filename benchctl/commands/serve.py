"""`benchctl serve mcp`."""

import typer

from .. import errors
from .base import SuggestingGroup

serve_app = typer.Typer(cls=SuggestingGroup, help="Serve workbench to other programs")


@serve_app.command("mcp")
def serve_mcp() -> None:
    """Serve the workbench as typed MCP tools over stdio (optional extra: pip install -e '.[mcp]'). Each tool runs the same benchctl command, so the governor and `--yes` rules apply."""
    try:
        from .. import mcp_server
    except ImportError as e:
        typer.echo(errors.render("WB-EXTRA-MISSING", "the MCP package is not installed"), err=True)
        raise typer.Exit(1) from e
    mcp_server.serve()
