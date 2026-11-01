#!/usr/bin/env bash
# Refuse a wrong git identity before it reaches GitHub, which credits a commit by the ID in a noreply address
# (a placeholder such as ID 1 belongs to a stranger, and a merged PR keeps the credit for good).
#
#   check-identity.sh            the identity git would use for the next commit (author and committer)
#   check-identity.sh --unpushed every commit on a local branch that no remote has yet (pre-push)
#
# Rules: a real name (not x, you, test...), and an email that is either the GitHub noreply address of the repository
# owner (owner = the account in the `origin` URL), GitHub's own web-flow address, or matches a line of
# .git/identity-allow (one ERE per line, local, never committed: other contributors). Values are never printed.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 1
mode=${1:---config}

owner=$(git remote get-url origin 2>/dev/null | sed -nE 's#^(https://([^@/]*@)?github\.com/|git@github\.com:)([^/]+)/.*#\3#p')
owner=$(printf %s "$owner" | tr '[:upper:]' '[:lower:]')
allow=$(git rev-parse --git-path identity-allow)

bad=0
report() { printf 'IDENTITY: %s\n' "$1" >&2; bad=1; }

allowed() { # email matches a line of .git/identity-allow
  local re
  [ -f "$allow" ] || return 1
  while IFS= read -r re; do
    case "$re" in ''|'#'*) continue ;; esac
    printf %s "$1" | grep -qE -- "$re" && return 0
  done < "$allow"
  return 1
}

declare -A real_id  # login -> GitHub's ID, looked up once
id_matches() { # id login: false only when GitHub answers with a different ID (offline or no gh: not checked)
  command -v gh >/dev/null 2>&1 || return 0
  [ -n "${real_id[$2]+x}" ] || real_id[$2]=$(timeout 5 gh api "users/$2" --jq .id 2>/dev/null || true)
  [ -z "${real_id[$2]}" ] || [ "${real_id[$2]}" = "$1" ]
}

ok_identity() { # name email
  local name=$1 email=$2 login id
  [ -n "$name" ] && [ -n "$email" ] || { echo "name or email is empty"; return 1; }
  if printf %s "$name" | grep -qiE '^(x|y|z|you|user|test|name|me|your name|unknown)$' || [ "${#name}" -lt 2 ]; then
    echo "placeholder name"; return 1
  fi
  [ "$email" = "noreply@github.com" ] && return 0  # GitHub's web UI (merge buttons)
  allowed "$email" && return 0
  [[ $email =~ ^([0-9]+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com$ ]] || { echo "not a GitHub noreply address"; return 1; }
  id=${BASH_REMATCH[1]%+}
  login=$(printf %s "${BASH_REMATCH[2]}" | tr '[:upper:]' '[:lower:]')
  if [ -n "$owner" ] && [ "$login" != "$owner" ]; then echo "noreply address of another GitHub account (not the repository owner)"; return 1; fi
  # GitHub credits the commit to the account with this ID, whatever the login in the address says.
  if [ -n "$id" ] && ! id_matches "$id" "$login"; then echo "the ID in the noreply address is not this account's ID"; return 1; fi
  return 0
}

split_ident() { # "Name <email> ts tz" -> name, email
  name=${1%% <*}; email=${1#*<}; email=${email%%>*}
}

case "$mode" in
  --config)
    for kind in AUTHOR COMMITTER; do
      split_ident "$(git var GIT_${kind}_IDENT 2>/dev/null)"
      why=$(ok_identity "$name" "$email") || report "$(printf %s "$kind" | tr '[:upper:]' '[:lower:]') identity refused: $why"
    done ;;
  --unpushed)
    while IFS=$'\t' read -r sha an ae cn ce; do
      why=$(ok_identity "$an" "$ae") || report "${sha:0:7} author refused: $why"
      why=$(ok_identity "$cn" "$ce") || report "${sha:0:7} committer refused: $why"
    done < <(git log --branches --not --remotes --format='%H%x09%an%x09%ae%x09%cn%x09%ce') ;;
  *) echo "usage: check-identity.sh [--config|--unpushed]" >&2; exit 2 ;;
esac

if [ "$bad" = 1 ]; then
  cat >&2 <<MSG
Fix: git config user.name "Your Name"; git config user.email "<id>+<your-github-login>@users.noreply.github.com"
(the id is in https://github.com/settings/emails). Commits already made with a wrong identity: rewrite them before pushing
(git rebase -r --exec 'git commit --amend --reset-author --no-edit' <base>). Another contributor's address: add an ERE line to .git/identity-allow.
MSG
  exit 1
fi
