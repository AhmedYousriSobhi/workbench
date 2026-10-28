#!/usr/bin/env bash
# Real-host tests: user cgroups (systemd-run --user), Docker, a live governor process.
# Not part of validate.sh because they need Docker and a systemd user session.
set -eu
cd "$(dirname "$0")/.."
[ -n "${PYLIBS:-}" ] && export PYTHONPATH="$PYLIBS${PYTHONPATH:+:$PYTHONPATH}"
HPCSIM_REAL=1 exec python3 -m pytest -v tests/real "$@"
