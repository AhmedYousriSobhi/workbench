#!/usr/bin/env bash
# Run before every commit: ./scripts/validate.sh   (install as hook: ./scripts/validate.sh --install-hook)
# Set PYLIBS=/path to prepend a dir of pip --target packages (pytest, ruff, pyyaml) to PYTHONPATH.
set -u
cd "$(dirname "$0")/.."

if [ "${1:-}" = "--install-hook" ]; then
  exec ./scripts/install-hooks.sh
fi

[ -n "${PYLIBS:-}" ] && export PYTHONPATH="$PYLIBS${PYTHONPATH:+:$PYTHONPATH}"
fail=0
step() { printf '== %s\n' "$1"; }
bad()  { printf 'FAIL: %s\n' "$1"; fail=1; }

step "secrets"
# staged + tracked files must not contain tokens
if git grep -nE 'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}' -- . ':!scripts/validate.sh' >/dev/null; then
  bad "token-like string in tracked files"
fi

step "privacy"
# public repo: no personal paths, emails, tokens or local-only tooling in tracked files (scripts/check-privacy.sh)
./scripts/check-privacy.sh --all || bad "privacy scan"
[ "$(git config core.hooksPath)" = ".githooks" ] || echo "NOTE  git hooks not installed: ./scripts/install-hooks.sh"

step "python compile"
py=$(git ls-files '*.py')
# shellcheck disable=SC2086
[ -z "$py" ] || python3 -m py_compile $py || bad "py_compile"

step "ruff"
RUFF=$(command -v ruff || true)
[ -z "$RUFF" ] && [ -x "${PYLIBS:-}/bin/ruff" ] && RUFF="$PYLIBS/bin/ruff"
if [ -n "$RUFF" ]; then
  "$RUFF" check . || bad "ruff check"
else echo "skip: ruff not installed"; fi

step "bash -n"
for f in $(git ls-files '*.sh' '.githooks/*'); do bash -n "$f" || bad "bash -n $f"; done
if command -v shellcheck >/dev/null; then
  # shellcheck disable=SC2046
  shellcheck -S warning $(git ls-files '*.sh' '.githooks/*') || bad "shellcheck"
fi

step "yaml"
python3 - <<'PY' || bad "yaml parse"
import subprocess, sys, yaml
files = subprocess.check_output(["git", "ls-files", "*.yml", "*.yaml"], text=True).split()
rc = 0
for f in files:
    if f.startswith("k8s/fixtures/"):  # multi-doc
        loader = yaml.safe_load_all
    else:
        loader = yaml.safe_load_all
    try:
        list(loader(open(f)))
    except Exception as e:
        print(f"{f}: {e}"); rc = 1
sys.exit(rc)
PY

step "progress docs"
python3 -m simctl.progress --check || bad "docs/PROGRESS.md or progress.html is stale"

step "json"
for f in $(git ls-files '*.json'); do python3 -m json.tool "$f" >/dev/null || bad "json $f"; done

step "systemd units"
if command -v systemd-analyze >/dev/null; then
  for f in $(git ls-files 'systemd/*.service'); do
    systemd-analyze verify "$PWD/$f" 2>&1 | grep -v "^$" | grep -iE "error|fail" && bad "unit $f"
  done
fi

step "pytest"
if python3 -c "import coverage" 2>/dev/null; then
  # coverage floor: the unit tests must keep covering the code (what needs root or a live host is in tests/real)
  python3 -m coverage run --source=simctl,governor,webui,components -m pytest -q tests || bad "pytest"
  COV_FLOOR="${COV_FLOOR:-90}"
  python3 -m coverage report --skip-empty --fail-under="$COV_FLOOR" > cov-report.txt || bad "coverage below ${COV_FLOOR}%"
  tail -3 cov-report.txt
  rm -f cov-report.txt
  rm -f .coverage
else
  python3 -m pytest -q tests || bad "pytest"
  echo "note: install coverage to enforce the coverage floor"
fi

step "governor smoke"
python3 -m governor --dry-run --once >/dev/null 2>&1 || bad "governor dry-run --once"

if [ $fail -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "VALIDATION FAILED"; fi
exit $fail
