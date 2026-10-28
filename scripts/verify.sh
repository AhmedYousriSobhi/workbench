#!/usr/bin/env bash
# One command to verify a change before asking for a merge. Runs the static checks, then the
# real-host tests, and prints a summary you can paste into the PR.
#   ./scripts/verify.sh           validate.sh + real tests that touch the governor/Docker (fast, ~1 min)
#   ./scripts/verify.sh --full    also the live stack lifecycle (up/scale/down, ~3 min, ~3 GB)
#   ./scripts/verify.sh --only X  run only real tests matching pytest -k X (skips validate.sh)
# Root-only tests (scripts/real-test-root.sh) still need `sudo`; the summary says when to run them.
set -u
cd "$(dirname "$0")/.."
full=0 only=""
while [ $# -gt 0 ]; do
  case "$1" in
    --full) full=1 ;;
    --only) only="$2"; shift ;;
    *) echo "usage: $0 [--full] [--only KEYWORD]" >&2; exit 2 ;;
  esac
  shift
done
log=$(mktemp); trap 'rm -f "$log"' EXIT
status=0
step() {  # step NAME CMD...: run, record, keep the output only if it failed
  local name="$1"; shift
  if "$@" >"$log" 2>&1; then echo "PASS  $name  ($(tail -1 "$log"))"; else
    echo "FAIL  $name"; tail -30 "$log"; status=1; fi
}
if [ -z "$only" ]; then step "validate.sh" ./scripts/validate.sh; fi
sel=(); [ -n "$only" ] && sel=(-k "$only") || { [ "$full" = 0 ] && sel=(-k "not stack"); }
step "real tests ${sel[*]:-}" ./scripts/real-test.sh -q "${sel[@]}"
[ "$full" = 0 ] && [ -z "$only" ] && echo "NOTE  live stack lifecycle skipped; run --full when stack/compose/slurm changed"
echo "NOTE  root-only checks: sudo ./scripts/real-test-root.sh (when governor cgroup/systemd code changed)"
exit $status
