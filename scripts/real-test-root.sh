#!/usr/bin/env bash
# Root-only real tests. Run with:  sudo env PYLIBS="$PYLIBS" ./scripts/real-test-root.sh
# Creates only throwaway objects (hpcsim-test-* containers and transient units) and removes them.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
cd "$(dirname "$0")/.."
[ -n "${PYLIBS:-}" ] && export PYTHONPATH="$PYLIBS${PYTHONPATH:+:$PYTHONPATH}"
HPCSIM_REAL=1 exec python3 -m pytest -v -p no:cacheprovider tests/real/test_root.py "$@"
