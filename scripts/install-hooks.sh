#!/usr/bin/env bash
# Point git at the tracked hooks in .githooks/ (once per clone). Needs no sudo. Undo: git config --unset core.hooksPath
set -eu
cd "$(dirname "$0")/.."
chmod +x .githooks/* scripts/check-privacy.sh
git config core.hooksPath .githooks
echo "hooks installed: pre-commit (branch guard, privacy scan, validate.sh), commit-msg (issue ref, privacy), pre-push (no main/dev, privacy)"
echo "extra local-only patterns to block (e.g. your employer's name): one regex per line in .git/privacy-deny"
