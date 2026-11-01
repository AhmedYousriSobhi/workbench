#!/usr/bin/env bash
# One command to verify a change before asking for a merge. Runs the static checks, then the
# real-host tests, and prints a summary you can paste into the PR.
#   ./scripts/verify.sh           validate.sh + real tests that touch the governor/Docker (fast, ~1 min)
#   ./scripts/verify.sh --full    also the live stack lifecycle (up/scale/down, ~3 min, ~3 GB)
#   ./scripts/verify.sh --only X  run only real tests matching pytest -k X (skips validate.sh)
#   ./scripts/verify.sh --unit X  verify one component: tests/test_X*.py (fast), then tests/real/test_X*.py (live)
#        --clean                  with --unit: stop the cluster first and run scripts/verify.d/X.sh (reset that unit's state)
#        --list-units             the units there are tests for;  -n / --dry-run  print what would run, run nothing
# Root-only tests (scripts/real-test-root.sh) still need `sudo`; the summary says when to run them.
# Your workbench selection (compose/generated/workbench.yaml, profile, sim.yaml) is saved first and put back on exit.
set -u
cd "$(dirname "$0")/.."
full=0 only="" unit="" unit_given=0 clean=0 list=0 dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --full) full=1 ;;
    --only) only="$2"; shift ;;
    --unit) unit="${2:-}"; unit_given=1; shift ;;
    --clean) clean=1 ;;
    --list-units) list=1 ;;
    -n|--dry-run) dry=1 ;;
    *) echo "usage: $0 [--full] [--only KEYWORD] [--unit NAME [--clean]] [--list-units] [-n]" >&2; exit 2 ;;
  esac
  shift
done
shopt -s nullglob
[ -n "${PYLIBS:-}" ] && export PYTHONPATH="$PYLIBS${PYTHONPATH:+:$PYTHONPATH}"
units() {  # test_bmc_tls_real.py -> bmc_tls: one name per component that has tests
  local f; for f in tests/test_*.py tests/real/test_*.py; do f=$(basename "$f" .py); f=${f#test_}; f=${f%_real}; echo "${f%_browser}"; done | sort -u
}
if [ "$list" = 1 ]; then units; exit 0; fi
fast=() live=() hook=""
if [ "$unit_given" = 1 ]; then
  case "$unit" in *[!a-z0-9_]*|"") echo "unit names are lower case letters, digits and _; known: $(units | tr '\n' ' ')" >&2; exit 2 ;; esac
  fast=(tests/test_"$unit"*.py) live=(tests/real/test_"$unit"*.py)
  if [ $((${#fast[@]} + ${#live[@]})) = 0 ]; then echo "no tests for unit '$unit'; known: $(units | tr '\n' ' ')" >&2; exit 2; fi
  [ -f "scripts/verify.d/$unit.sh" ] && hook="scripts/verify.d/$unit.sh"
  if [ "$dry" = 1 ]; then
    [ "$clean" = 1 ] && { echo "would run: ./scripts/benchctl down --yes"; [ -n "$hook" ] && echo "would run: $hook"; }
    [ ${#fast[@]} -gt 0 ] && echo "would run: pytest ${fast[*]}"
    [ ${#live[@]} -gt 0 ] && echo "would run: WORKBENCH_REAL=1 pytest ${live[*]}"
    exit 0
  fi
fi
. scripts/lib-selection.sh
log=$(mktemp)
selection_save  # the real tests build and forget their own selections: keep yours, so the next `up` is your cluster, not std
trap 'rm -f "$log"; selection_restore' EXIT
status=0
step() {  # step NAME CMD...: run, record, keep the output only if it failed
  local name="$1"; shift
  if "$@" >"$log" 2>&1; then echo "PASS  $name  ($(tail -1 "$log"))"; else
    echo "FAIL  $name"; tail -30 "$log"; status=1; fi
}
if [ "$unit_given" = 1 ]; then
  if [ "$clean" = 1 ]; then
    step "clean start (benchctl down)" ./scripts/benchctl down --yes
    [ -n "$hook" ] && step "reset $unit state ($hook)" "$hook"
  fi
  [ ${#fast[@]} -gt 0 ] && step "$unit unit tests" python3 -m pytest -q "${fast[@]}"
  if [ ${#live[@]} -gt 0 ]; then step "$unit live tests" env WORKBENCH_REAL=1 python3 -m pytest -q "${live[@]}"
  else echo "NOTE  $unit has no live tests (tests/real/test_${unit}*.py)"; fi
else
  if [ -z "$only" ]; then step "validate.sh" ./scripts/validate.sh; fi
  sel=(); [ -n "$only" ] && sel=(-k "$only") || { [ "$full" = 0 ] && sel=(-k "not stack"); }
  step "real tests ${sel[*]:-}" ./scripts/real-test.sh -q "${sel[@]}"
  [ "$full" = 0 ] && [ -z "$only" ] && echo "NOTE  live stack lifecycle skipped; run --full when stack/compose/slurm changed"
fi
case "$selection_kept" in *workbench.yaml*)
  echo "NOTE  your workbench selection was kept; the tests stopped the cluster, so bring it back with ./scripts/benchctl up (or start)" ;;
esac
echo "NOTE  root-only checks: sudo ./scripts/real-test-root.sh (when governor cgroup/systemd code changed)"
exit $status
