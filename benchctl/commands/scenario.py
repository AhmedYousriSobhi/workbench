"""`benchctl scenario list|run`."""

import typer

from .. import completion, contract, errors, inventory, pagefeed, scenario, slurmtrace, stack
from .base import JSON, OUTPUT, SuggestingGroup, _sim

scenario_app = typer.Typer(cls=SuggestingGroup, help="Run named fault and workload scenarios (scenarios/*.yaml)")


@scenario_app.command("list")
def scenario_list(output: str = OUTPUT, json_: bool = JSON) -> None:
    """Reference (SC-nn), name, step count and description of every scenario; `run` takes the name or the reference."""
    if contract.wanted(output, json_) == "json":
        typer.echo(contract.dumps(inventory.scenario_rows()))
        return
    for sc in scenario.list_all():
        typer.echo(f"{sc.get('ref', '-'):6} {sc['name']:26} {len(sc.get('steps', [])):2} steps  {sc.get('description', '')}")


@scenario_app.command("run")
def scenario_run(name: str = typer.Argument(..., autocompletion=completion.scenarios, help="see `scenario list`"),
                 speed: float = typer.Option(1.0, help="fast-forward: 10 plays it ten times faster"),
                 dry_run: bool = typer.Option(False, "--dry-run", help="validate and show the timeline only"),
                 trace: bool = typer.Option(False, "--trace", help="after each step print job states and the new Slurm log lines"),
                 output: str = OUTPUT, json_: bool = JSON) -> None:
    """Play a scenario against the running stack; exit 1 if a step or expectation fails. `--json` prints progress on stderr and one result object on stdout."""
    as_json = contract.wanted(output, json_) == "json"

    def say(msg: str) -> None:
        typer.echo(msg, err=as_json)
    sim = _sim()
    try:
        sc = scenario.load(name)
    except scenario.ScenarioError as e:
        typer.echo(errors.render("WB-UNKNOWN-SCENARIO", completion.explain(name, completion.scenarios(""), "scenario")) if completion.scenarios("") else str(e), err=True)
        raise typer.Exit(1) from e
    try:
        sc = scenario.selectors.resolve_scenario(sc, sim)  # `first gpu node` becomes this cluster's node name
    except scenario.ScenarioError as e:
        typer.echo(f"invalid: {e}", err=True)
        raise typer.Exit(1) from e
    problems = scenario.validate(sc, sim)
    for p in problems:
        typer.echo(f"invalid: {p}", err=True)
    if problems:
        raise typer.Exit(1)
    say(scenario.summary(sc))
    if dry_run:
        timeline = scenario.plan(sc)
        if as_json:
            typer.echo(contract.dumps({"scenario": sc["name"], "dry_run": True, "timeline": [{"at": s.at, "target": s.target, "do": s.do} for s in timeline]}))
        for s in [] if as_json else timeline:
            typer.echo(f"[{s.at:6.1f}s] {s.target} {s.do}")
        raise typer.Exit(0)
    if scenario.disruptive(sc) and not stack.governor_admits(0, say):  # GV-9: nothing starts at L2 and above
        typer.echo("not run: the governor admits no new work at this memory level (see `benchctl status`)", err=True)
        raise typer.Exit(1)
    tracer = slurmtrace.Tracer(slurmtrace.nodes_of(sc), say=say) if trace else None
    feed = pagefeed.Feed(sc, speed, say)  # the web page shows the run when it answers; never waits for it (ADR 0086 phase 4)

    def on_step(step, outcome) -> None:
        feed.step(step, outcome)
        if tracer:
            tracer.step(step, outcome)
    try:
        report = scenario.run(sc, speed=speed, dilation=float(sim.get("time_dilation", 1.0)), say=say, on_step=on_step, on_expect=feed.expect)
    except KeyboardInterrupt:
        feed.close(None, True)
        raise
    feed.close(report.ok, report.stopped)
    say("scenario " + ("passed" if report.ok else "FAILED"))
    if as_json:
        def row(o) -> dict:
            return {"label": o.label, "status": o.status, "detail": o.detail}
        typer.echo(contract.dumps({"scenario": report.name, "passed": report.ok, "stopped": report.stopped,
                                   "steps": [row(o) for o in report.steps], "expects": [row(o) for o in report.expects]}))
    raise typer.Exit(0 if report.ok else 1)
