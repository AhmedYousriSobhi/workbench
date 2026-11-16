#!/usr/bin/env bash
# Real-host tests: user cgroups (systemd-run --user), Docker, a live governor process.
# Not part of validate.sh because they need Docker and a systemd user session.
set -eu
cd "$(dirname "$0")/.."
[ -n "${PYLIBS:-}" ] && export PYTHONPATH="$PYLIBS${PYTHONPATH:+:$PYTHONPATH}"
# Explicit module files (verify.sh --changed) replace the whole folder; anything else is added to it.
targets=(tests/real)
for a in "$@"; do case "$a" in tests/real/test_*.py) targets=() ;; esac; done
. scripts/lib-interrupt.sh  # Ctrl+C, SIGTERM, SIGHUP: one SIGINT to pytest so its teardown stops the stacks it started (issue 819)
export WORKBENCH_REAL=1
ir_run python3 -m pytest -v -rfEs --durations=25 "${targets[@]}" "$@"
