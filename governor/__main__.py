"""Governor entry point: `python -m governor [--dry-run] [--once] [--socket PATH]`."""
import argparse
import json
import logging
import os
import pwd
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import control, metrics
from .annotate import Annotator
from .core import Governor
from .executors import ARRIVALS_FLAG, Executor, UserExecutor, discover_docker
from .ladder import Ladder, Thresholds
from .memlock import MitigationPath, lock_memory, locked_kb, raise_memlock_limit
from .planner import Group
from .signals import GB, CgroupSampler, FileSampler, Sampler
from .watchdog import Watchdog

log = logging.getLogger("governor")


def arrivals_flag_path(given: str, user_mode: bool) -> str:
    """The flag file defaults to /run/hpcsim, which only root can write: rootless mode uses the user's runtime dir."""
    if user_mode and given == ARRIVALS_FLAG:
        return str(Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/hpcsim-{os.getuid()}") / "hpcsim" / "arrivals.paused")
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
    ap.add_argument("--arrivals-flag", default=ARRIVALS_FLAG)
    ap.add_argument("--no-watchdog", action="store_true", help="disable container security/limit checks")
    ap.add_argument("--watch-interval", type=float, default=15.0, help="seconds between security scans")
    ap.add_argument("--grafana-url", default=None,
                    help="publish audit events as annotations; token from $HPCSIM_GRAFANA_TOKEN")
    ap.add_argument("--refresh-interval", type=float, default=30.0, help="seconds between inventory refreshes")
    ap.add_argument("--workdir", default=None,
                    help="repo root; enables scale/set-profile by running simctl there")
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
    ladder = Ladder(Thresholds(**json.loads(args.thresholds or "{}")))
    executor = (UserExecutor if args.user_mode else Executor)(groups, cgroups, path, arrivals_flag=arrivals_flag_path(args.arrivals_flag, args.user_mode))
    watchdog = None if args.no_watchdog else Watchdog()
    token = os.environ.get("HPCSIM_GRAFANA_TOKEN", "")
    annotator = Annotator(args.grafana_url, token) if args.grafana_url and token else None
    if args.grafana_url and not token:
        log.warning("--grafana-url given but HPCSIM_GRAFANA_TOKEN is empty: annotations disabled")
    def effector(a: list[str]) -> int:
        cmd = [sys.executable, "-m", "simctl.cli", *a]
        owner = Path(args.workdir).stat().st_uid
        if os.geteuid() == 0 and owner != 0:  # run as the checkout's owner so generated files stay theirs
            cmd = ["runuser", "-u", pwd.getpwuid(owner).pw_name, "--", *cmd]
        return subprocess.run(cmd, cwd=args.workdir, capture_output=True, timeout=600, check=False).returncode

    gov = Governor(sampler, ladder, groups, executor, args.dry_run, watchdog, args.watch_interval,
                   annotator, discover_docker, args.refresh_interval,
                   effector if args.workdir else None)
    srv = control.serve(args.socket, gov.handle, gov.group_names, gid=args.socket_gid) \
        if args.socket else None
    msrv = None
    if args.metrics_addr:
        try:
            msrv = metrics.serve(args.metrics_addr, gov)
            log.info("metrics on http://%s/metrics", args.metrics_addr)
        except OSError as e:  # observability must not stop the governor
            log.error("metrics endpoint unavailable on %s: %s", args.metrics_addr, e)
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
            time.sleep(args.interval)
    finally:
        if msrv:
            msrv.shutdown()
        if srv:
            srv.shutdown()
            if args.socket and os.path.exists(args.socket):
                os.unlink(args.socket)
        path.close()


if __name__ == "__main__":
    main()
