#!/usr/bin/env bash
# One command to verify a change before asking for a merge. Runs the static checks, then the
# real-host tests, and prints a summary you can paste into the PR.
#   ./scripts/verify.sh           validate.sh (~2 min) + real tests that touch the governor/Docker; every step prints its seconds.
#                                 validate.sh is skipped when it already passed for this exact tree (the commit hook ran it);
#                                 the step line says so. Durations: see the per-step seconds in your own run (issue 237).
#   ./scripts/verify.sh --full    also the live stack lifecycle (up/scale/down, ~3 GB)
#   ./scripts/verify.sh --only X  run only real tests matching pytest -k X (skips validate.sh)
#        --changed [BASE]         live tests only for the modules the change can affect (tests/real/impact.yaml; BASE default origin/dev),
#                                 with --failed-first --maxfail=1; an unmatched path selects everything. Before a PR to dev (issue 576).
#        --explain                with --changed: print file -> rule -> modules and exit
#        --serial                 run validate.sh before the live tests (default: in the background, next to them)
#        --trust-ci               skip validate.sh when the CI run for HEAD passed and the tree is clean
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
changed=0 base="origin/dev" explain=0 serial=0 trustci=0 full=0 revalidate=0 nobrowser=0 only="" unit="" unit_given=0 clean=0 list=0 dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --full) full=1 ;;
    --changed) changed=1; case "${2:-}" in ""|-*) ;; *) base="$2"; shift ;; esac ;;
    --explain) explain=1 ;;
    --serial) serial=1 ;;
    --trust-ci) trustci=1 ;;
    --only) only="$2"; shift ;;
    --unit) unit="${2:-}"; unit_given=1; shift ;;
    --clean) clean=1 ;;
    --revalidate) revalidate=1 ;;
    --skip-browser) nobrowser=1 ;;
    --list-units) list=1 ;;
    -n|--dry-run) dry=1 ;;
    *) echo "usage: $0 [--full] [--changed [BASE]] [--explain] [--serial] [--trust-ci] [--only KEYWORD] [--revalidate] [--skip-browser] [--unit NAME [--clean]] [--list-units] [-n]" >&2; exit 2 ;;
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
modules=() pick="" pickwhy=""
if [ "$changed" = 1 ] && [ "$unit_given" = 0 ]; then
  [ "$explain" = 1 ] && { python3 scripts/impact.py --base "$base" --explain; exit 0; }
  pick=$(python3 scripts/impact.py --base "$base" --explain) || pick="ALL"
  sel_line=$(printf '%s\n' "$pick" | tail -1)
  if [ "$sel_line" = "ALL" ]; then full=1; pickwhy="every module ($(printf '%s\n' "$pick" | grep -m1 'ALL (' | sed 's/.*-> ALL (//; s/)$//'))"
  else mapfile -t modules < <(printf '%s\n' "$sel_line" | grep .); pickwhy="${#modules[@]} modules"; fi
  basesha=$(git rev-parse --short "$base" 2>/dev/null || echo "?")
  echo "SELECT $pickwhy  (base $base@$basesha)"
fi
validate_mode="in the background"; [ "$serial" = 1 ] && validate_mode="first (--serial)"
if [ "$dry" = 1 ] && [ "$unit_given" = 0 ]; then
  echo "would run: validate.sh $validate_mode$([ "$trustci" = 1 ] && echo ', skipped when CI passed for HEAD and the tree is clean')"
  if [ "$changed" = 1 ] && [ "$sel_line" != "ALL" ]; then
    [ ${#modules[@]} = 0 ] && echo "would run: no live tests (nothing live is affected)" || echo "would run: pytest --failed-first --maxfail=1 $(printf 'tests/real/%s.py ' "${modules[@]}")"
  else echo "would run: pytest tests/real$([ "$full" = 0 ] && echo " -k 'not stack'")"; fi
  exit 0
fi
. scripts/lib-selection.sh
log=$(mktemp)
selection_save  # the real tests build and forget their own selections: keep yours, so the next `up` is your cluster, not std
vlog=$(mktemp) vpid=""
trap 'rm -f "$log" "$vlog"; [ -n "$vpid" ] && kill "$vpid" 2>/dev/null; selection_restore' EXIT
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
  grep '^SLOW ' "$log" | sed 's/^/      /'  # per-module seconds from tests/real/verify_times.py (issue 576)
  timings+=("${secs}s $name")
}
validate_start() { vt0=$SECONDS; echo "START validate.sh (in the background, own log)"; ./scripts/validate.sh >"$vlog" 2>&1 & vpid=$!; }
validate_wait() {  # the live tests are done: collect validate.sh; either failing fails the run
  [ -n "$vpid" ] || return 0
  local rc; wait "$vpid"; rc=$?; vpid=""
  local secs=$((SECONDS - vt0))
  if [ $rc = 0 ]; then echo "PASS  validate.sh  [${secs}s, ran next to the live tests]  ($(tail -1 "$vlog"))"; else
    echo "FAIL  validate.sh  [${secs}s]"; tail -40 "$vlog"; status=1
    mkdir -p compose/generated; keep="compose/generated/verify-fail-validate-$(date +%H%M%S).log"; cp "$vlog" "$keep"; echo "      full output of this step: $keep"; fi
  timings+=("${secs}s validate.sh")
}
ci_green() {  # CI passed for exactly this commit and nothing is modified: its result is this tree's result
  command -v gh >/dev/null 2>&1 || return 1
  [ -z "$(git status --porcelain)" ] || return 1
  [ "$(gh run list --commit "$(git rev-parse HEAD)" --workflow ci.yml --json conclusion -q '.[0].conclusion' 2>/dev/null)" = success ]
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
  run_validate=0
  if [ -z "$only" ]; then
    if [ "$revalidate" = 0 ] && age=$(stamp_fresh); then echo "SKIP  validate.sh  (passed ${age}s ago for this exact tree; --revalidate runs it again)"
    elif [ "$trustci" = 1 ] && ci_green; then echo "SKIP  validate.sh  (CI passed for HEAD and the tree is clean, --trust-ci)"
    else run_validate=1; fi
  fi
  if [ "$run_validate" = 1 ] && [ "$serial" = 1 ]; then step "validate.sh" ./scripts/validate.sh; run_validate=0; fi
  [ "$run_validate" = 1 ] && validate_start
  # the tests start their own stacks: a cluster running with your selection would meet them half way (issue 535)
  step "clean start (benchctl down)" ./scripts/benchctl down --yes
  for f in $SELECTION_FILES; do rm -f "$f"; done
  sel=()
  if [ "$changed" = 1 ] && [ "${sel_line:-ALL}" != "ALL" ]; then
    sel=(--failed-first --maxfail=1); for m in "${modules[@]}"; do sel+=("tests/real/$m.py"); done
  elif [ "$changed" = 1 ]; then sel=(--failed-first --maxfail=1)
  elif [ -n "$only" ]; then sel=(-k "$only")
  elif [ "$full" = 0 ]; then sel=(-k "not stack"); fi
  [ "$nobrowser" = 1 ] && sel+=(-m "not browser")
  if [ "$changed" = 1 ] && [ "${sel_line:-ALL}" != "ALL" ] && [ ${#modules[@]} = 0 ]; then echo "SKIP  live tests  (no live module is affected by this change)"
  else step "real tests ${sel[*]:-}" ./scripts/real-test.sh "${sel[@]}"; fi
  validate_wait
  [ "$full" = 0 ] && [ -z "$only" ] && [ "$changed" = 0 ] && echo "NOTE  live stack lifecycle skipped; run --full when stack/compose/slurm changed"
  [ "$changed" = 1 ] && echo "NOTE  --changed ran the modules the change can reach; run --full before a dev -> main merge"
fi
case "$selection_kept" in *workbench.yaml*)
  echo "NOTE  your workbench selection was kept; the tests stopped the cluster, so bring it back with ./scripts/benchctl up (or start)" ;;
esac
echo "TIME  total ${SECONDS}s: ${timings[*]:-no steps}"
echo "NOTE  root-only checks: sudo ./scripts/real-test-root.sh (when governor cgroup/systemd code changed)"
exit $status
