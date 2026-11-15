# 0010. A rootless governor mode, so starting needs no sudo

Status: accepted

## Context
Starting the app must not require sudo. Everything in the start/build path runs as the user (Docker group) except
the governor, which was a root systemd service because it writes cgroup files (freeze, `memory.high`, reclaim,
`cgroup.kill`), calls `mlockall` without a limit, and runs with `OOMScoreAdjust=-900`.

## Decision
`python -m governor --user-mode` runs as the user. `UserExecutor` replaces the cgroup files with the Docker API
(the user is in the `docker` group): freeze/thaw become `docker pause/unpause`, stop becomes `docker kill` then `docker stop`. Admission
(GV-9), the escalation ladder, the security watchdog, Grafana annotations, `/metrics` and the control socket all work
unchanged; the socket lives under `$XDG_RUNTIME_DIR/workbench/` and `benchctl`/the web page find it automatically when no
system socket exists. `./scripts/benchctl start` starts it (`BENCHCTL_GOVERNOR=auto|user|system|off`) and `stop` ends it.
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

## Verified under real conditions (as an ordinary user)
`tests/real/test_user_mode_real.py`: a real container filling its 200 MB cap (NF-1) is warned about, paused and killed
before the kernel OOM killer; on the live stack real containers are paused at L3, stopped at L4, un-paused when calm,
and a stopped one comes back with the `start` command. Running them found four defects, now fixed:
- `docker stop -t 2` loses the race to the OOM killer: a runaway PID 1 ignores SIGTERM for the 2 s grace. Rootless stop
  is now `docker kill` (SIGKILL, like `cgroup.kill` in the root path) followed by `docker stop`.
- The arrivals flag defaulted to `/run/workbench/` (root-only), so every L1 action failed; it now defaults to the user's
  runtime directory in rootless mode. (Issue 476: nothing read that flag. With `--workdir`, both governors now write it
  to `compose/generated/flags/arrivals.paused`; the login nodes and jira-mock mount that directory read-only at
  `/run/workbench-flags`, `user-sim` skips an arrival and jira-mock skips a background change while it exists. Never
  `/run/workbench`: `gov.sock` lives there, and the web page and containers get no socket, W-3.)
- The one-cgroup test sampler crashed the governor when the watched container's cgroup vanished.
- Containers the governor stopped dropped out of the inventory, so `start NAME` was refused as an unknown group in both
  modes. They are now remembered until they run again. A stop is still never undone automatically (by design).
A shared host also matters: if a system governor runs on the same machine it governs the same containers from host
signals, so rootless tests need that one calm (L0).

## Addendum: systemd-oomd backstop on the project's own slice (issue 686, SPEC GV-10)
Owner decision, 2026-11-14 (#471). The backstop SPEC GV-10 names, `systemd-oomd`, is shipped as an option of the root installer and nothing else:
- **What:** `systemd/workbench.slice.d/50-oomd.conf` sets `ManagedOOMMemoryPressure=kill` and `ManagedOOMMemoryPressureLimit=60%`. Compose puts every container in `workbench-pN.slice` (`cgroup_parent`) and systemd nests a dashed slice under `workbench.slice`, so one drop-in covers P0 to P6 and nothing else on the host. The governor's own slice (`workbench-governor.slice`) is not under it.
- **Opt-in:** `sudo ./scripts/install-governor.sh --with-oomd` (with the service) or `--oomd-only`; a plain install never adds it. `--without-oomd` removes only it, `--uninstall` removes it with the service. `./scripts/install-governor.sh --print-oomd` shows it without root. The start path (`benchctl start`, rootless) is unchanged (NF-5).
- **Never the host's config:** `/etc/systemd/oomd.conf` is not read or written. The installer does not enable `systemd-oomd` either; if it is not running the drop-in does nothing, and the installer and `benchctl doctor` (`oomd backstop`) say so with the command. Doctor reports off (the default), active, installed but oomd not running, or not applied.
- **Limits:** the 60% limit is read by oomd against the 20 s default duration of the host's oomd configuration (not changed here). oomd picks the victim by its own ranking inside `workbench.slice`; per-class preference (`ManagedOOMPreference`) is not set, so the governor's ladder (stop P6 first) remains the intended path and oomd only acts if pressure is still high when it fails.
- **Tested:** unit text, installer dry run and doctor line are hermetic tests (`tests/test_oomd.py`); `tests/real/test_root.py::test_oomd_backstop_installs_covers_the_class_slices_and_uninstalls` runs with `sudo ./scripts/real-test-root.sh` and is run by the owner.

## Addendum: the Docker Engine API on a connection opened at startup, not the docker CLI (issue 452, SPEC GV-8)
- **What:** the user-mode executor used to fork `docker pause|unpause|kill|stop|start`, a new process exactly when memory is scarce. `governor/dockerapi.py` now sends the same requests (`POST /containers/NAME/pause|unpause|kill|stop?t=2|start`) over the Docker Unix socket (`$DOCKER_HOST` when it is `unix://`, else `/var/run/docker.sock`) on one kept-alive connection opened at startup. The L3 freeze and the L4 kill and stop fork nothing; the root `Executor` uses the same client for its `stop` (after `cgroup.kill`) and `start`.
- **Same behaviour:** the daemon's 409 "already paused", "is not paused" and "is not running" are tolerated where the CLI path tolerated them; 304 for an already stopped or started container is fine; any other refusal raises with the daemon's own message. `--dry-run` never connects.
- **Fallback:** if the socket cannot be opened at startup (a `tcp://` host, rootless Docker on another socket, no daemon) the governor logs one warning and uses the CLI as before. A connection that drops later is reopened once (a socket, not a process); if that fails the action fails and is audited.
- **What it does not change:** user mode still cannot `mlockall` without a limit or shield its OOM score, Python still allocates while it builds a request, and discovery (`docker ps` and `inspect` at startup and every refresh interval) still runs the CLI. SPEC GV-8 stays "met by the system service; rootless mode is best effort": the guarantee is not changed, one avoidable fork is removed.
- **Tested:** `tests/test_user_mode.py` (a fake daemon on a Unix socket: no `subprocess` call on freeze, thaw, stop, start; one connection; tolerated and failing answers; reconnect; fallback). The live run (`./scripts/real-test.sh`, `tests/real/test_user_mode_real.py`) is for the owner.

## Addendum: skipped verbs are audited; rootless Docker rejected (issue 453, SPEC GV-6)
Owner decision (issue 453): keep the ladder above and do not recommend rootless Docker as the fix for the missing throttle and reclaim, because its bridge network lives in a separate namespace and the host could not reach the fixed 10.141.x addresses (SPEC NF-8).
- **What changed:** a verb the user-mode executor cannot do (`throttle`, `unthrottle`, `reclaim`) used to leave one `log.info` line and nothing else, so the audit log and the page showed "L3 reclaim" as if memory had been freed. `UserExecutor` now returns a note and the governor records `SKIPPED <action>: skipped in rootless mode: ... needs root ...` in the audit log on every skipped action (so it also reaches the page's audit view and the Grafana annotations, GV-6) and counts it in `counts["actions_skipped"]`. The log line stays once per verb.
- **Unchanged:** the ladder, thresholds, and the root executor (it returns nothing, so it never reports a skip).
- **Tested:** `tests/test_user_mode.py` (hermetic); `tests/real/test_user_mode_real.py` now asserts the `SKIPPED` entry (written, not run here).
