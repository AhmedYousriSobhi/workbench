"""`benchctl measure run|overhead|cgroup|app|report`."""

import typer

from .. import measure, memory

measure_app = typer.Typer(help="Phase 0: read-only memory measurement")


@measure_app.command("run")
def measure_run(label: str = typer.Argument(..., help="e.g. idle, loaded"),
                duration: float = typer.Option(30, help="seconds to sample"),
                interval: float = typer.Option(5, help="seconds between samples")) -> None:
    """Sample host, containers, processes (PSS) and VMs; save docs/measurements/<label>.json."""
    snaps = measure.sample(duration, interval)
    typer.echo(f"{len(snaps)} samples -> {measure.save(label, snaps)}")


@measure_app.command("overhead")
def measure_overhead(save: bool = typer.Option(False, "--save", help="keep it as this machine's default for the plan (compose/generated, gitignored)")) -> None:
    """Docker's own memory (containerd-shims, dockerd, containerd), which `docker stats` does not count; run it with the stack up."""
    raw = measure.docker_overhead()
    ov = measure.overhead_summary({f"overhead:{k}": {"peak": v} for k, v in raw.items()})
    if not ov:
        typer.echo("no containerd-shim is running: start the stack (`benchctl up`) and retry", err=True)
        raise typer.Exit(1)
    typer.echo(f"{ov['shims']} containers: {ov['per_container_mb']} MB each for the shim; dockerd + containerd {ov['fixed_mb']} MB fixed "
               + (f"(private memory, not exact PSS, for {raw['estimated']} root-owned process(es); `sudo ./scripts/benchctl measure overhead` shows the exact PSS, do not `--save` it with sudo)" if raw["estimated"] else "(exact PSS)"))
    if save:
        typer.echo(f"saved: {memory.save_overhead(ov['per_container_mb'], ov['fixed_mb'])}; the plan adds {ov['per_container_mb']} MB for every container a start creates (delete the file to stop)")
    else:
        typer.echo("keep it for the plan: rerun with --save")


CGROUP_DEFAULT = ["mariadb", "prometheus", "teleport"]
CGROUP_NAMES = typer.Argument(None, help=f"container names (default {' '.join(CGROUP_DEFAULT)}; the compose project prefix is added)")


def _mb(b: int) -> str:
    return f"{b / 1024**2:.0f} MB"


@measure_app.command("cgroup")
def measure_cgroup(name: list[str] = CGROUP_NAMES) -> None:
    """Per container: memory in use, the tmpfs part (`shmem`), page cache, anon and OOM kills, from its cgroup (read-only, no root). Issue 455: MariaDB moves off tmpfs only if shmem is 50 MB or more."""
    typer.echo(f"{'container':28}{'current':>9}{'shmem':>9}{'file':>9}{'anon':>9}{'limit':>9}  oom_kill")
    missing = 0
    for short in name or CGROUP_DEFAULT:
        full = f"workbench-{short}-1" if not short.startswith("workbench-") else short
        m = measure.cgroup_memory(full)
        if m is None:
            missing += 1
            typer.echo(f"{full:28}not running (or its cgroup is not readable)")
            continue
        typer.echo(f"{full:28}{_mb(m['current']):>9}{_mb(m['shmem']):>9}{_mb(m['file']):>9}{_mb(m['anon']):>9}"
                   f"{(_mb(m['limit']) if m['limit'] else 'none'):>9}  {m['oom_kill']}")
    if missing:
        raise typer.Exit(1)


APP_MATCH = typer.Option(None, "--match", help="process name or command-line text of the app (repeatable)")
APP_PID = typer.Option(None, "--pid", help="a process id of the app (repeatable)")
APP_SAVE = typer.Option(False, "--save", help="keep the suggested reserve as this machine's default (compose/generated, gitignored)")


@measure_app.command("app")
def measure_app_cmd(match: list[str] = APP_MATCH, pid: list[int] = APP_PID,
                    duration: float = typer.Option(120, help="seconds to sample; 0 runs until Ctrl-C"),
                    interval: float = typer.Option(2, min=0.1, help="seconds between samples"),
                    save: bool = APP_SAVE) -> None:
    """Measure the app under test (PSS of all its processes): peak, typical use and a suggested --app-mb."""
    if not match and not pid:
        typer.echo("give the app with --match NAME or --pid N (repeatable); the app must be running", err=True)
        raise typer.Exit(2)
    if not measure.app_bytes(match or [], pid or [])[1]:
        typer.echo(f"no running process matches {', '.join(match or [])} {' '.join(map(str, pid or []))}; start the app and retry", err=True)
        raise typer.Exit(1)
    caught = measure.app_breakdown(match or [], pid or [])
    typer.echo("matched: " + ", ".join(f"{n} x{c} ({b // 1024**2} MB)" for n, (b, c) in list(caught.items())[:6]) + (" ..." if len(caught) > 6 else ""))
    if len(caught) > measure.APP_MAX_NAMES:
        typer.echo(f"warning: {len(caught)} different programs match; a loose --match counts other programs' memory. Narrow it (a path or full name) before using --save", err=True)
    typer.echo("sampling" + (f" for {duration:g} s" if duration else " until Ctrl-C") + "; use the app as you normally would ...")
    stats = measure.app_summary(measure.sample_app(match or [], pid or [], duration, interval))
    if not stats:
        typer.echo("the app exited before the first sample; start it and retry", err=True)
        raise typer.Exit(1)
    mib = 1024**2
    typer.echo(f"{stats['samples']} samples, up to {stats['processes']} process(es)\n"
               f"  peak     {stats['peak'] // mib:>7} MB\n  typical  {stats['typical'] // mib:>7} MB (median)\n"
               f"  suggested --app-mb {stats['suggested_mb']} (peak + 10%, rounded up to 100 MB)")
    if save:
        typer.echo(f"saved {stats['suggested_mb']} MB as the default reserve: {memory.save_app(stats['suggested_mb'])} (delete the file to return to {memory.APP_MB})")
    else:
        typer.echo("keep it as this machine's default: rerun with --save")


@measure_app.command("report")
def measure_report(idle: str = typer.Argument(..., help="label of the idle run"),
                   loaded: str = typer.Argument(None, help="label of the loaded run")) -> None:
    """Markdown table: idle vs loaded peak with suggested budgets (peak x 1.2)."""
    import json
    from pathlib import Path

    def load(label: str) -> dict:
        return json.loads((Path("docs/measurements") / f"{label}.json").read_text())

    typer.echo(measure.report(load(idle), load(loaded) if loaded else None))
