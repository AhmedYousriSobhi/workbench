"""Governor entry point: `python -m governor [--dry-run] [--once]`."""
import argparse
import logging
import time

from .ladder import Ladder
from .planner import Action, Group, plan, reversible_first
from .signals import GB, Sampler

log = logging.getLogger("governor")


def execute(acts: list[Action], dry_run: bool) -> None:
    for a in acts:
        if dry_run:
            log.info("dry-run: would %s", a)
        else:  # real executors (docker/cgroup/virsh) land with the container work
            raise NotImplementedError("executors not implemented; use --dry-run")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="governor")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="sample once and exit")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    sampler, ladder = Sampler(), Ladder()
    groups: list[Group] = []  # populated from the compose/profile inventory later
    prev = 0
    while True:
        now = time.monotonic()
        s = sampler.sample(now)
        level = ladder.update(s, now)
        log.info("L%d avail=%.2fGB some=%.1f%% full=%.1f%%", level,
                 s.mem_available / GB, s.psi_some_avg10, s.psi_full_avg10)
        if level != prev:
            execute(reversible_first(plan(level, groups)), args.dry_run)
            prev = level
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
