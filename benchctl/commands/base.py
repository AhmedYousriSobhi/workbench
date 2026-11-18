"""Pieces every command group shares: the verb-suggesting group, the output options, the sim.yaml readers, the confirmation, the node arguments, the daemon and health-check helpers."""

import difflib
import sys

import click
import typer
from typer.core import TyperGroup

from .. import agentmode, completion, errors, nhc, scenario, stack, workbench


class CodedUsageError(click.UsageError):
    def __init__(self, message: str, code: str, ctx=None):
        super().__init__(message, ctx=ctx)
        self.code = code


class SuggestingGroup(TyperGroup):
    """A mistyped verb gets a hint and an error (exit 2). It is never guessed and run: `benchctl dwon` must not
    run `down` (CLI guidelines: suggest, do not silently correct; no prefix abbreviations that block future verbs)."""

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError as e:
            close = difflib.get_close_matches(args[0], self.list_commands(ctx), n=1) if args else []
            hint = f" Did you mean '{close[0]}'?" if close else ""
            raise CodedUsageError(f"{e.message}{hint}", "WB-UNKNOWN-VERB", ctx=ctx) from e

    def main(self, *args, **kwargs):
        """Agent mode (BENCHCTL_AGENT=1): a usage error is one coded line on stderr, not a usage box (ADR 0065)."""
        agentmode.apply()
        if not agentmode.agent():
            return super().main(*args, **kwargs)
        kwargs["standalone_mode"] = False
        try:
            code = super().main(*args, **kwargs)
        except Exception as e:  # typer 0.27 carries its own copy of click's exceptions: match them by shape
            if type(e).__name__ == "Abort":
                sys.exit(1)
            if not (hasattr(e, "exit_code") and hasattr(e, "format_message")):
                raise
            sys.stderr.write(errors.click_line(e) + "\n")
            sys.exit(e.exit_code)
        sys.exit(code if isinstance(code, int) else 0)


def _profile_check(value: str | None) -> str | None:
    return completion.check_value(value, completion.profiles(""), "profile")


def _node_check(value: str) -> str:
    return completion.check_value(value, completion.nodes(""), "node")


def _target_check(value: str) -> str:
    return completion.check_value(value, completion.targets(""), "node or service")


OUTPUT = typer.Option("table", "-o", "--output", autocompletion=completion.outputs, help="table, json (for scripts) or name (one per line)")
JSON = typer.Option(False, "--json", help="same as -o json: one JSON object with a contract_version (docs/agents/schemas)")


def _base_sim() -> dict:
    from pathlib import Path

    import yaml

    return yaml.safe_load(Path("sim.yaml").read_text())


def _sim() -> dict:
    """sim.yaml with the workbench last built applied (node counts), so every command sees what is really there."""
    base = _base_sim()
    try:
        spec = workbench.current(base)
    except workbench.SpecError:
        return base
    return workbench.effective(base, spec)[0] if spec else base


YES = typer.Option(False, "-y", "--yes", help="do not ask (a terminal asks; scripts are never asked; BENCHCTL_AGENT=1 requires it)")


def _confirm(what: str, yes: bool, ask: bool = True) -> None:
    """Ask before a step that ends shells or removes nodes, but only for a person at a terminal: scripts and the
    launcher (no TTY) are never blocked, and `--yes` skips the question. With BENCHCTL_AGENT=1 nobody can be asked, so
    the step is refused without `--yes` (ADR 0067); `ask=False` adds only that rule, for verbs that never asked a person."""
    if not yes and agentmode.agent():
        errors.needs_yes(what)
        raise typer.Exit(1)
    if ask and not yes and sys.stdin.isatty() and not typer.confirm(f"{what} Continue?", default=False):
        typer.echo("cancelled", err=True)
        raise typer.Exit(1)


NODE = typer.Argument(..., autocompletion=completion.nodes, callback=_node_check, help="node name, e.g. cn001 or gpu001")


TARGET = typer.Argument(..., autocompletion=completion.targets, callback=_target_check,
                        help="node or service, e.g. cn001, gpu001, slurmctld")


CHECK = typer.Argument(..., autocompletion=completion.checks, help="check name, see `benchctl get checks`")


SETS = typer.Option(None, "--set", help="a parameter, key=value (repeat for several)")


POSITION = typer.Option(None, "--position", help="place in the run order, 1 = first")


def _nhc_done(what: str) -> None:
    """The change is in the files; deliver it to the nodes' mounted copy and say so (a node reads it at its next run)."""
    if errs := nhc.sync():
        typer.echo("\n".join(errs), err=True)
        raise typer.Exit(1)
    typer.echo(f"{what}. Running nodes use it at their next health check (within 30 s); `benchctl get checks` shows the order.")


def _nhc_run(fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except nhc.NhcError as e:
        typer.echo(f"refused, nothing changed:\n{e}", err=True)
        raise typer.Exit(1) from e


def _daemon(do: dict, what: str) -> None:
    o = scenario.execute(scenario.Step(0, "slurm", do), host=True, runner=lambda c: stack._run(c))
    typer.echo(what if o.status == "ok" else f"cannot {what}: {o.detail}", err=o.status != "ok")
    raise typer.Exit(0 if o.status == "ok" else 1)
