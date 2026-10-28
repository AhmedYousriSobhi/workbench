"""Governor entry point: `python -m governor [--dry-run] [--once] [--socket PATH]`."""
import argparse
import json
import logging
import os
import signal
import time
from pathlib import Path

from . import control
from .annotate import Annotator
from .core import Governor
from .executors import ARRIVALS_FLAG, Executor, discover_docker
from .ladder import Ladder, Thresholds
from .memlock import MitigationPath, lock_memory, locked_kb, raise_memlock_limit
from .planner import Group
from .signals import GB, CgroupSampler, FileSampler, Sampler
from .watchdog import Watchdog

log = logging.getLogger("governor")


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
    executor = Executor(groups, cgroups, path, arrivals_flag=args.arrivals_flag)
    watchdog = None if args.no_watchdog else Watchdog()
    token = os.environ.get("HPCSIM_GRAFANA_TOKEN", "")
    annotator = Annotator(args.grafana_url, token) if args.grafana_url and token else None
    if args.grafana_url and not token:
        log.warning("--grafana-url given but HPCSIM_GRAFANA_TOKEN is empty: annotations disabled")
    gov = Governor(sampler, ladder, groups, executor, args.dry_run, watchdog, args.watch_interval,
                   annotator, discover_docker, args.refresh_interval)
    srv = control.serve(args.socket, gov.handle, gov.group_names, gid=args.socket_gid) \
        if args.socket else None
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(0)))
    try:
        raise_memlock_limit()
        lock_memory()
        log.info("memory locked: %d kB", locked_kb())
    except OSError as e:
        log.error("mlockall failed: %s", e)
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
        if srv:
            srv.shutdown()
            if args.socket and os.path.exists(args.socket):
                os.unlink(args.socket)
        path.close()


if __name__ == "__main__":
    main()
