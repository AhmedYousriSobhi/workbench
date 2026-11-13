"""Governor entry point: `python -m governor [--dry-run] [--once] [--socket PATH]`."""
import argparse
import json
import logging
import os
import pwd
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from . import control, metrics
from .annotate import Annotator
from .core import Governor
from .executors import ARRIVALS_FLAG, Executor, UserExecutor, discover_docker
from .hostsize import derive, read_host
from .ladder import Ladder, Thresholds
from .memlock import MitigationPath, lock_memory, locked_kb, raise_memlock_limit
from .planner import Group
from .psi import PsiTrigger
from .signals import GB, CgroupSampler, FileSampler, Sampler
from .watchdog import Watchdog

log = logging.getLogger("governor")


FLAGS_DIR = "compose/generated/flags"  # mounted read-only into the login nodes and jira-mock (issue 476)


def clear_stale_flag(flag: str, dry_run: bool) -> bool:
    """The ladder starts at L0, so a pause flag found at startup is left over from a governor that died at L1: remove it, or
    arrivals stay paused until someone deletes the file. A dry run touches nothing."""
    f = Path(flag)
    if dry_run or not f.exists():
        return False
    f.unlink(missing_ok=True)
    return True


def arrivals_flag_path(given: str, user_mode: bool, workdir: str | None = None) -> str:
    """Where L1 puts the pause flag. With --workdir (root service and user mode alike) it is the checkout's flags directory,
    which the containers mount read-only (never /run/workbench: gov.sock lives there). Without one, the old defaults."""
    if given == ARRIVALS_FLAG and workdir:
        return str(Path(workdir) / FLAGS_DIR / "arrivals.paused")
    if user_mode and given == ARRIVALS_FLAG:
        return str(Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/workbench-{os.getuid()}") / "workbench" / "arrivals.paused")
    return given


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="governor")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="sample once and exit")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--socket", default=None, help="control socket path (default: none)")
    ap.add_argument("--socket-gid", type=int, default=None)
    ap.add_argument("--vm", action="append", default=[], metavar="DOMAIN=PRIO",
                    help="libvirt domain managed via virsh managedsave (repeatable)")
    ap.add_argument("--signals-file", default=None, help="drive the ladder from a JSON file (tests)")
    ap.add_argument("--watch-cgroup", default=None, help="take signals from one cgroup dir (tests)")
    ap.add_argument("--thresholds", default=None, help='JSON overrides, e.g. {"l4_mem": 1000000}')
    ap.add_argument("--sim", default=None, help="sim.yaml whose host.reserve_gb / safety_floor_gb set the memory thresholds (default: WORKDIR/sim.yaml)")
    ap.add_argument("--no-psi-trigger", action="store_true", help="poll only: do not ask the kernel to wake the loop on a memory stall")
    ap.add_argument("--arrivals-flag", default=ARRIVALS_FLAG)
    ap.add_argument("--no-watchdog", action="store_true", help="disable container security/limit checks")
    ap.add_argument("--watch-interval", type=float, default=15.0, help="seconds between security scans")
    ap.add_argument("--grafana-url", default=None,
                    help="publish audit events as annotations; token from $WORKBENCH_GRAFANA_TOKEN")
    ap.add_argument("--refresh-interval", type=float, default=30.0, help="seconds between inventory refreshes")
    ap.add_argument("--workdir", default=None,
                    help="repo root; enables scale/set-profile by running benchctl there")
    ap.add_argument("--metrics-addr", default=None, metavar="HOST:PORT",
                    help="serve Prometheus /metrics here (bind the docker bridge gateway, not 0.0.0.0)")
    ap.add_argument("--user-mode", action="store_true",
                    help="rootless: pause/stop through the Docker API instead of cgroup files (no sudo; ADR 0010)")
    ap.add_argument("--require-lock", action="store_true", help="exit if mlockall fails")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    path = MitigationPath()  # pre-allocate before locking so the pages are included (GV-8)
    groups, cgroups = discover_docker()
    groups += [Group(n, int(p), "vm") for n, p in (v.split("=") for v in args.vm)]
    log.info("inventory: %s", [(g.name, g.prio) for g in groups])
    if args.signals_file:
        sampler = FileSampler(Path(args.signals_file))
    elif args.watch_cgroup:
        sampler = CgroupSampler(Path(args.watch_cgroup))
    else:
        sampler = Sampler()
    sim_path = Path(args.sim) if args.sim else Path(args.workdir) / "sim.yaml" if args.workdir else None
    reserve, floor = read_host(sim_path) if sim_path else read_host(Path("/nonexistent"))
    ladder = Ladder(Thresholds(**{**derive(reserve, floor), **json.loads(args.thresholds or "{}")}))
    log.info("memory thresholds from reserve %.1f GB, floor %.1f GB: L4 < %.2f, L1 < %.2f, calm > %.2f GB",
             reserve, floor, ladder.t.l4_mem / GB, ladder.t.l1_mem / GB, ladder.t.l0_mem / GB)
    executor = (UserExecutor if args.user_mode else Executor)(groups, cgroups, path, arrivals_flag=arrivals_flag_path(args.arrivals_flag, args.user_mode, args.workdir))
    if clear_stale_flag(executor.arrivals_flag, args.dry_run):
        log.warning("removed a stale arrivals pause flag left by an earlier governor (%s)", executor.arrivals_flag)
    watchdog = None if args.no_watchdog else Watchdog()
    token = os.environ.get("WORKBENCH_GRAFANA_TOKEN", "")
    annotator = Annotator(args.grafana_url, token) if args.grafana_url and token else None
    if args.grafana_url and not token:
        log.warning("--grafana-url given but WORKBENCH_GRAFANA_TOKEN is empty: annotations disabled")
    def effector(a: list[str]) -> int:
        cmd = [sys.executable, "-m", "benchctl.cli", *a]
        owner = Path(args.workdir).stat().st_uid
        if os.geteuid() == 0 and owner != 0:  # run as the checkout's owner so generated files stay theirs
            cmd = ["runuser", "-u", pwd.getpwuid(owner).pw_name, "--", *cmd]
        return subprocess.run(cmd, cwd=args.workdir, capture_output=True, timeout=600, check=False).returncode

    def streamer(a: list[str], on_line: Callable[[str], None]) -> int:
        """Like the effector, but hands each output line over as it appears so the governor can report stages (issue 519)."""
        cmd = [sys.executable, "-m", "benchctl.cli", *a]
        owner = Path(args.workdir).stat().st_uid
        if os.geteuid() == 0 and owner != 0:
            cmd = ["runuser", "-u", pwd.getpwuid(owner).pw_name, "--", *cmd]
        proc = subprocess.Popen(cmd, cwd=args.workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                env={**os.environ, "WORKBENCH_STAGE_LINES": "1"})
        timer = threading.Timer(600, proc.kill)  # the effector's 10-minute limit
        timer.start()
        try:
            for line in proc.stdout:
                on_line(line.rstrip("\n"))
            return proc.wait()
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()

    def tracer(nodes: list[str], since: float) -> dict:
        cmd = [sys.executable, "-m", "benchctl.cli", "get", "trace", "--since", str(max(1.0, time.time() - since)),
               *[a for n in nodes for a in ("--node", n)]]
        owner = Path(args.workdir).stat().st_uid
        if os.geteuid() == 0 and owner != 0:
            cmd = ["runuser", "-u", pwd.getpwuid(owner).pw_name, "--", *cmd]
        r = subprocess.run(cmd, cwd=args.workdir, capture_output=True, timeout=60, check=False, text=True)
        if r.returncode:
            raise RuntimeError((r.stderr or r.stdout).strip()[:200])
        return json.loads(r.stdout)

    def statser() -> dict:
        cmd = [sys.executable, "-m", "benchctl.cli", "get", "slurm-stats"]
        owner = Path(args.workdir).stat().st_uid
        if os.geteuid() == 0 and owner != 0:
            cmd = ["runuser", "-u", pwd.getpwuid(owner).pw_name, "--", *cmd]
        r = subprocess.run(cmd, cwd=args.workdir, capture_output=True, timeout=30, check=False, text=True)
        if r.returncode:
            raise RuntimeError((r.stderr or r.stdout).strip()[:200])
        return json.loads(r.stdout)

    def capturer(node: str, seconds: int):
        """`benchctl get capture NODE` as the repository's owner, its JSON lines on stdout (issue 505)."""
        cmd = [sys.executable, "-m", "benchctl.cli", "get", "capture", node, "--seconds", str(seconds)]
        owner = Path(args.workdir).stat().st_uid
        if os.geteuid() == 0 and owner != 0:
            cmd = ["runuser", "-u", pwd.getpwuid(owner).pw_name, "--", *cmd]
        return subprocess.Popen(cmd, cwd=args.workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    gov = Governor(sampler, ladder, groups, executor, args.dry_run, watchdog, args.watch_interval,
                   annotator, discover_docker, args.refresh_interval,
                   effector if args.workdir else None, tracer=tracer if args.workdir else None,
                   statser=statser if args.workdir else None, capturer=capturer if args.workdir else None,
                   streamer=streamer if args.workdir else None, admit_floor=int(floor * GB))
    srv = control.serve(args.socket, gov.handle, gov.group_names, gid=args.socket_gid) \
        if args.socket else None
    msrv = None
    if args.metrics_addr:
        try:
            msrv = metrics.serve(args.metrics_addr, gov)
            log.info("metrics on http://%s/metrics", args.metrics_addr)
        except OSError as e:  # observability must not stop the governor
            log.error("metrics endpoint unavailable on %s: %s", args.metrics_addr, e)
    trigger = None if args.once or args.no_psi_trigger or args.signals_file or args.watch_cgroup else PsiTrigger()
    if trigger:
        log.info("PSI trigger active: the loop wakes on a memory stall") if trigger.active else log.info("PSI trigger off, polling every %.1f s (%s)", args.interval, trigger.why)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(0)))
    try:
        raise_memlock_limit()
        lock_memory()
        log.info("memory locked: %d kB", locked_kb())
    except (OSError, ValueError) as e:
        (log.info if args.user_mode else log.error)("mlockall failed: %s", e)  # normal without root
        if args.require_lock:
            raise SystemExit(2)
    try:
        while True:
            level = gov.step(time.monotonic())
            s = gov.signals
            log.info("L%d avail=%.2fGB some=%.1f%% full=%.1f%%", level,
                     s.mem_available / GB, s.psi_some_avg10, s.psi_full_avg10)
            if args.once:
                return
            if trigger:
                trigger.wait(args.interval)
            else:
                time.sleep(args.interval)
    finally:
        if msrv:
            msrv.shutdown()
        if srv:
            srv.shutdown()
            if args.socket and os.path.exists(args.socket):
                os.unlink(args.socket)
        path.close()
        if trigger:
            trigger.close()
        if hasattr(sampler, "close"):
            sampler.close()


if __name__ == "__main__":
    main()
