"""`benchctl submit|exec|ssh|logs|scale`: act on one node or service, or change the compute-node count."""

import os
import sys

import typer

from .. import agentmode, completion, errors, nodes, stack, workbench
from .base import TARGET, YES, _base_sim, _confirm, _profile_check, _sim
from .groups import app


@app.command("submit")
def submit(node: str = TARGET, gpus: int = typer.Option(0, "--gpus", help="GPUs the job holds (GPU nodes)"),
           cpus: int = typer.Option(1, "--cpus", help="CPUs the job holds"),
           duration: int = typer.Option(300, "--duration", help="seconds the job runs (10 to 3600)"),
           user: str = typer.Option(None, "-u", "--user", autocompletion=completion.users, help="simulated user, default the first in sim.yaml")) -> None:
    """Submit a job on one node: `benchctl submit gpu001 --gpus 4 --duration 600`. It sleeps for the duration, holding its CPUs
    and GPUs, so squeue lists it, the node shows allocated and the GPU metrics show those GPUs busy."""
    from .. import workload

    try:
        cmd = workload.command(_sim(), node, gpus, duration, user, cpus)
    except workload.WorkloadError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    r = stack._run(cmd)
    out = (r.stdout or r.stderr or "").strip()
    typer.echo(f"{node}: job {out} submitted for {duration} s" if r.returncode == 0 else f"cannot submit on {node}: {out}",
               err=r.returncode != 0)
    raise typer.Exit(r.returncode)


@app.command("exec", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def exec_cmd(ctx: typer.Context, node: str = TARGET,
             user: str = typer.Option(None, "-u", "--user", autocompletion=completion.users, help="run as this simulated user, e.g. alice")) -> None:
    """Run a command on a node, or open a shell: `benchctl exec gpu001 -- nvidia-smi`, `benchctl exec cn001`."""

    cmd = ctx.args or ["bash"]
    tty = ["-it"] if sys.stdin.isatty() and not ctx.args else []
    os.execvp("docker", ["docker", "exec", *tty, *(["-u", user] if user else []), f"{stack.PROJECT}-{node}-1", *cmd])


@app.command("ssh", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def ssh_cmd(ctx: typer.Context, target: str = typer.Argument(..., help="NODE for a root shell, USER@NODE for a login as that user, e.g. cn001 or alice@cn001")) -> None:
    """Open a shell on a node: `benchctl ssh cn001` (root), `benchctl ssh alice@cn001` (that user over SSH). A command after `--` runs instead."""

    from .. import nodeshell

    try:
        node = nodeshell.split(target)[1]
        known = completion.targets("")
        if not known:
            raise ValueError("the cluster is not running; start it with ./scripts/benchctl start")
        if node not in known:
            raise ValueError(f"unknown node {node!r}; try `benchctl get nodes`")
        cmd = nodeshell.argv(target, _sim(), str(stack.SSH_DIR / "id_ed25519"), ctx.args, sys.stdin.isatty())
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    os.execvp(cmd[0], cmd)


@app.command()
def logs(node: str = TARGET, follow: bool = typer.Option(False, "-f", "--follow"),
         tail: int = typer.Option(50, "--tail")) -> None:
    """Container log of a node or service. With BENCHCTL_AGENT=1 the log goes to a file and only its path is printed."""
    from pathlib import Path

    if agentmode.agent():
        if follow:
            raise errors.CodedBadParameter("--follow never ends: read a bounded log with --tail N", "WB-USAGE")
        r = stack._run(["docker", "logs", "--tail", str(tail), f"{stack.PROJECT}-{node}-1"])
        out = Path("compose/generated/logs")
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{node}.log"
        path.write_text(r.stdout + r.stderr)
        typer.echo(f"{path} ({len((r.stdout + r.stderr).splitlines())} lines, last {tail} of {node}); read it with: tail -n 40 {path}")
        raise typer.Exit(r.returncode)
    os.execvp("docker", ["docker", "logs", *(["-f"] if follow else []), "--tail", str(tail),
                         f"{stack.PROJECT}-{node}-1"])


@app.command()
def scale(n: int = typer.Argument(..., min=0, help="compute node count"),
          profile: str = typer.Option(None, "--profile", autocompletion=completion.profiles, callback=_profile_check, help="default: the profile `up` last started"),
          dry_run: bool = typer.Option(False, "-n", "--dry-run", help="say which nodes start or stop; change nothing"),
          yes: bool = YES) -> None:
    """Change the compute-node count: regenerates slurm.conf, restarts slurmctld, starts/stops slurmd."""
    if workbench.current(_base_sim()):
        typer.echo("a workbench is built: to change its size run `benchctl up --cpu-nodes N ...` "
                   "(or edit its file and `benchctl apply -f FILE`); `scale` works on a size preset (`up --profile`)")
        raise typer.Exit(1)
    now = stack.running_nodes()
    want = [name for name, kind in nodes.node_names(_sim(), n) if kind == "compute"]
    stop = [x for x in now if x not in want]
    if dry_run:
        typer.echo(f"would start: {', '.join(x for x in want if x not in now) or 'none'}; "
                   f"would stop and remove: {', '.join(stop) or 'none'}")
        return
    if stop:
        _confirm(f"This removes {', '.join(stop)}; shells and jobs on them end.", yes)
    raise typer.Exit(0 if stack.scale(_sim(), profile or stack.current_profile(), n, say=typer.echo) else 1)
