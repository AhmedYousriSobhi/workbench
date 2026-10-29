# 0010. A rootless governor mode, so starting needs no sudo

Status: accepted

## Context
Starting the app must not require sudo. Everything in the start/build path runs as the user (Docker group) except
the governor, which was a root systemd service because it writes cgroup files (freeze, `memory.high`, reclaim,
`cgroup.kill`), calls `mlockall` without a limit, and runs with `OOMScoreAdjust=-900`.

## Decision
`python -m governor --user-mode` runs as the user. `UserExecutor` replaces the cgroup files with the Docker API
(the user is in the `docker` group): freeze/thaw become `docker pause/unpause`, stop becomes `docker stop`. Admission
(GV-9), the escalation ladder, the security watchdog, Grafana annotations, `/metrics` and the control socket all work
unchanged; the socket lives under `$XDG_RUNTIME_DIR/hpcsim/` and `simctl`/the web page find it automatically when no
system socket exists. `./scripts/opti start` starts it (`OPTI_GOVERNOR=auto|user|system|off`) and `stop` ends it.
The root service stays available as an optional upgrade and wins when it is running.

## What rootless mode does not give
- No `memory.high` throttle (L2) and no `memory.reclaim` (L3): those verbs are skipped and logged once. The ladder
  still escalates to freeze (stops growth, frees no RAM) and stop (frees RAM).
- No guarantee the governor's own pages stay resident (`mlockall` is limited for a user) and no OOM-score shield, so
  under extreme pressure it can be slower or killed before the containers it protects. SPEC GV-8 is only met by the
  root service.
- It cannot act on resources outside Docker's reach that need root.

## Consequences
- The default start path needs no privilege; protection is weaker but real. Users who want the full ladder install the
  root service once.
- The machine prerequisites (Docker installed, the user able to use it) are one-time host setup that
  `scripts/check-machine.sh` verifies; the app itself never asks for sudo.
- SPEC GV-8 now reads "met by the system service; rootless mode is best effort".
