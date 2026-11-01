#!/usr/bin/env bash
# Initial machine check: run BEFORE building. Needs only the system python3 (no venv, no packages).
# Exits 1 if the machine cannot build or run workbench.
here=${BASH_SOURCE[0]%/*}; [ "$here" = "${BASH_SOURCE[0]}" ] && here=.
cd "$here/.." || exit 2
command -v python3 >/dev/null || { echo "FAIL: python3 not found (sudo apt install python3)"; exit 1; }
exec python3 -m benchctl.machine "$@"
