#!/usr/bin/env bash
# Point git at the tracked hooks in .githooks/ (once per clone). Needs no sudo. Undo: git config --unset core.hooksPath
set -eu
cd "$(dirname "$0")/.."
chmod +x .githooks/* scripts/check-privacy.sh scripts/check-identity.sh
git config core.hooksPath .githooks
echo "hooks installed: pre-commit (branch guard, identity, privacy scan, validate.sh), commit-msg (issue ref, privacy), pre-push (no main/dev, identity, privacy)"
if ./scripts/check-identity.sh >/dev/null 2>&1; then echo "git identity: ok (the repository owner's GitHub noreply address)"; else ./scripts/check-identity.sh; fi
echo "extra local-only patterns to block (e.g. your employer's name): one regex per line in .git/privacy-deny"
