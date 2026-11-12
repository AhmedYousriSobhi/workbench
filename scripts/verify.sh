#!/usr/bin/env bash
# One command to verify a change before asking for a merge. Runs the static checks, then the
# real-host tests, and prints a summary you can paste into the PR.
#   ./scripts/verify.sh           validate.sh (~2 min) + real tests that touch the governor/Docker; every step prints its seconds.
#                                 validate.sh is skipped when it already passed for this exact tree (the commit hook ran it);
#                                 the step line says so. Durations: see the per-step seconds in your own run (issue 237).
#   ./scripts/verify.sh --full    also the live stack lifecycle (up/scale/down, ~3 GB)
#   ./scripts/verify.sh --only X  run only real tests matching pytest -k X (skips validate.sh)
#        --revalidate             run validate.sh even when a fresh pass is recorded
#        --skip-browser           leave out the Chromium tests (pytest marker `browser`)
#   ./scripts/verify.sh --unit X  verify one component: tests/test_X*.py (fast), then tests/real/test_X*.py (live)
#        --clean                  with --unit: stop the cluster first and run scripts/verify.d/X.sh (reset that unit's state)
#        --list-units             the units there are tests for;  -n / --dry-run  print what would run, run nothing
# Root-only tests (scripts/real-test-root.sh) still need `sudo`; the summary says when to run them.
# Each step shows a live status line (elapsed, a tqdm-style bar with the percentage, tests done, the last test) so a long run is visibly alive.
# Your workbench selection (compose/generated/workbench.yaml, profile, sim.yaml) is saved first and put back on exit.
set -u
cd "$(dirname "$0")/.."
full=0 revalidate=0 nobrowser=0 only="" unit="" unit_given=0 clean=0 list=0 dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --full) full=1 ;;
    --only) only="$2"; shift ;;
    --unit) unit="${2:-}"; unit_given=1; shift ;;
    --clean) clean=1 ;;
    --revalidate) revalidate=1 ;;
    --skip-browser) nobrowser=1 ;;
    --list-units) list=1 ;;
    -n|--dry-run) dry=1 ;;
    *) echo "usage: $0 [--full] [--only KEYWORD] [--revalidate] [--skip-browser] [--unit NAME [--clean]] [--list-units] [-n]" >&2; exit 2 ;;
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
. scripts/lib-stamp.sh
timings=()
bar() {  # bar PCT: a 20-cell tqdm-style bar
  local p=$1 n=$(($1 / 5)) i out=""
  for ((i = 0; i < 20; i++)); do [ $i -lt $n ] && out+="#" || out+="."; done
  printf '[%s] %3d%%' "$out" "$p"
}
progress() {  # progress NAME T0 PID: one live status line while PID runs (in place on a terminal, a line per 20 s otherwise)
  local name="$1" t0="$2" pid="$3" tick=0 chunk pct done_n last line
  while kill -0 "$pid" 2>/dev/null; do
    sleep 1; tick=$((tick + 1))
    if [ -t 1 ] || [ $((tick % 20)) = 0 ]; then
      chunk=$(tail -c 20000 "$log" 2>/dev/null | tr '\r' '\n')
      pct=$(printf '%s\n' "$chunk" | grep -o '\[ *[0-9]\{1,3\}%\]' | tail -1 | tr -dc '0-9')
      done_n=$(grep -cE ' (PASSED|FAILED|SKIPPED|XFAIL|XPASS|ERROR)( |$)' "$log" 2>/dev/null)
      last=$(printf '%s\n' "$chunk" | grep -v '^[[:space:]]*$' | tail -1 | cut -c1-70)
      line="RUN   $name  [$((SECONDS - t0))s]"
      [ -n "$pct" ] && line="$line  $(bar "$pct")"
      [ "${done_n:-0}" -gt 0 ] && line="$line  ${done_n} tests done"
      [ -n "$last" ] && line="$line  | $last"
      if [ -t 1 ]; then printf '\r\033[K%s' "$(printf '%s' "$line" | cut -c1-"$(tput cols 2>/dev/null || echo 120)")"; else echo "$line"; fi
    fi
  done
  [ -t 1 ] && printf '\r\033[K'
}
step() {  # step NAME CMD...: run with a live status line, record, keep the output only if it failed; every line says how long it took
  local name="$1" t0 secs pid rc; shift; t0=$SECONDS
  echo "START $name"
  "$@" >"$log" 2>&1 & pid=$!
  progress "$name" "$t0" "$pid"
  wait "$pid"; rc=$?
  secs=$((SECONDS - t0))
  if [ $rc = 0 ]; then echo "PASS  $name  [${secs}s]  ($(tail -1 "$log"))"; else
    echo "FAIL  $name  [${secs}s]"; tail -40 "$log"; status=1
    mkdir -p compose/generated; keep="compose/generated/verify-fail-${name%% *}-$(date +%H%M%S).log"; cp "$log" "$keep"
    echo "      full output of this step: $keep  (attach it; nothing needs re-running with -vv)"; fi
  awk '/slowest .*durations/{f=1} f&&/^=+ .* in [0-9.]+s/{f=0} f' "$log" | sed 's/^/      /'  # where the time went (pytest --durations)
  timings+=("${secs}s $name")
}
if [ "$unit_given" = 1 ]; then
  if [ "$clean" = 1 ]; then
    step "clean start (benchctl down)" ./scripts/benchctl down --yes
    [ -n "$hook" ] && step "reset $unit state ($hook)" "$hook"
  fi
  [ ${#fast[@]} -gt 0 ] && step "$unit unit tests" python3 -m pytest -v "${fast[@]}"
  if [ ${#live[@]} -gt 0 ]; then step "$unit live tests" env WORKBENCH_REAL=1 python3 -m pytest -v -rfE --durations=25 "${live[@]}"
  else echo "NOTE  $unit has no live tests (tests/real/test_${unit}*.py)"; fi
else
  if [ -z "$only" ]; then
    if [ "$revalidate" = 0 ] && age=$(stamp_fresh); then echo "SKIP  validate.sh  (passed ${age}s ago for this exact tree; --revalidate runs it again)"
    else step "validate.sh" ./scripts/validate.sh; fi
  fi
  # the tests start their own stacks: a cluster running with your selection would meet them half way (issue 535)
  step "clean start (benchctl down)" ./scripts/benchctl down --yes
  for f in $SELECTION_FILES; do rm -f "$f"; done
  sel=(); [ -n "$only" ] && sel=(-k "$only") || { [ "$full" = 0 ] && sel=(-k "not stack"); }
  [ "$nobrowser" = 1 ] && sel+=(-m "not browser")
  step "real tests ${sel[*]:-}" ./scripts/real-test.sh "${sel[@]}"
  [ "$full" = 0 ] && [ -z "$only" ] && echo "NOTE  live stack lifecycle skipped; run --full when stack/compose/slurm changed"
fi
case "$selection_kept" in *workbench.yaml*)
  echo "NOTE  your workbench selection was kept; the tests stopped the cluster, so bring it back with ./scripts/benchctl up (or start)" ;;
esac
echo "TIME  total ${SECONDS}s: ${timings[*]:-no steps}"
echo "NOTE  root-only checks: sudo ./scripts/real-test-root.sh (when governor cgroup/systemd code changed)"
exit $status
