"""Governor entry point: `python -m governor [--dry-run] [--once] [--socket PATH]`."""
import argparse
import logging
import os
import signal
import time

from . import control
from .core import Governor
from .ladder import Ladder
from .memlock import MitigationPath, lock_memory, locked_kb, raise_memlock_limit
from .planner import Action, Group
from .signals import GB, Sampler

log = logging.getLogger("governor")


def no_executor(a: Action) -> None:
    raise NotImplementedError(f"no executor for {a.verb}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="governor")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="sample once and exit")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--socket", default=None, help="control socket path (default: none)")
    ap.add_argument("--socket-gid", type=int, default=None)
    ap.add_argument("--require-lock", action="store_true", help="exit if mlockall fails")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    groups: list[Group] = []  # populated from the compose/profile inventory later
    gov = Governor(Sampler(), Ladder(), groups, no_executor, args.dry_run)
    path = MitigationPath()  # pre-allocate before locking so the pages are included (GV-8)
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
