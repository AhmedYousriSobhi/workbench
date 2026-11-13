#!/usr/bin/env bash
# Real-host tests: user cgroups (systemd-run --user), Docker, a live governor process.
# Not part of validate.sh because they need Docker and a systemd user session.
set -eu
cd "$(dirname "$0")/.."
[ -n "${PYLIBS:-}" ] && export PYTHONPATH="$PYLIBS${PYTHONPATH:+:$PYTHONPATH}"
# Explicit module files (verify.sh --changed) replace the whole folder; anything else is added to it.
targets=(tests/real)
for a in "$@"; do case "$a" in tests/real/test_*.py) targets=() ;; esac; done
WORKBENCH_REAL=1 exec python3 -m pytest -v -rfE --durations=25 "${targets[@]}" "$@"
