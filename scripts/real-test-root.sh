#!/usr/bin/env bash
# Root-only real tests. Run with:  sudo ./scripts/real-test-root.sh   (uses .venv/bin/python when it exists; PYLIBS is still honoured)
# Creates only throwaway objects (workbench-test-* containers and transient units) and removes them.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
cd "$(dirname "$0")/.."
[ -n "${PYLIBS:-}" ] && export PYTHONPATH="$PYLIBS${PYTHONPATH:+:$PYTHONPATH}"
py=python3; [ -x .venv/bin/python ] && py=.venv/bin/python  # sudo has no pytest on the system python
. scripts/lib-interrupt.sh  # Ctrl+C, SIGTERM, SIGHUP: one SIGINT to pytest so it removes its throwaway objects (issue 819)
export WORKBENCH_REAL=1
ir_run "$py" -m pytest -v -p no:cacheprovider tests/real/test_root.py tests/real/test_netem_root.py "$@"
