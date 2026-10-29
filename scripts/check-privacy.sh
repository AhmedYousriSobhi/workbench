#!/usr/bin/env bash
# Privacy guard for a public repository: fails when a commit would publish personal data.
#   ./scripts/check-privacy.sh --staged          what is about to be committed (pre-commit hook)
#   ./scripts/check-privacy.sh --all             every tracked file (validate.sh, pre-push hook)
#   ./scripts/check-privacy.sh --message FILE    a commit message (commit-msg hook)
# It prints `file:line [rule]`, never the matched text, so a leak is not copied into logs or terminals.
# Rules: home-directory paths, the local username, personal emails, token / key / URL-credential patterns,
# local-only tooling paths (.claude/, graphify-out/, env and key files), symlinks that leave the repo, a
# non-noreply git identity (--staged), and any extra patterns in .git/privacy-deny (one ERE per line, never committed).
# Public third-party values that look like emails go in .privacy-allow (one exact string per line).
# The patterns below are written so they cannot match this file itself.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2
mode=${1:-}
msg=${2:-}
fail=0
report() { printf 'PRIVACY: %s\n' "$*" >&2; fail=1; }

case "$mode" in
  --staged)
    mapfile -t files < <(git diff --cached --name-only --diff-filter=ACMR)
    [ "${#files[@]}" -eq 0 ] && exit 0
    gsrc=(--cached); gfiles=("${files[@]}") ;;
  --all)
    mapfile -t files < <(git ls-files)
    gsrc=(); gfiles=(.) ;;
  --message)
    [ -f "$msg" ] || { echo "usage: $0 --message FILE" >&2; exit 2; }
    files=() ;;
  *) echo "usage: $0 --staged | --all | --message FILE" >&2; exit 2 ;;
esac

# Print "path:line:match" for an ERE (or a fixed word with -F -w) over the chosen source.
hits() {
  if [ "$mode" = --message ]; then grep -noI "$@" "$msg" | sed 's#^#message:#'
  else git grep "${gsrc[@]}" -nIo "$@" -- "${gfiles[@]}" 2>/dev/null; fi
}
# scan RULE ALLOW_ERE ERE...: report every hit whose matched text is not allowed.
scan() {
  local rule=$1 ok=$2 f l m; shift 2
  while IFS=: read -r f l m; do
    [ -n "$ok" ] && printf '%s' "$m" | grep -qE "$ok" && continue
    [ -f .privacy-allow ] && grep -qxF -- "$m" .privacy-allow && continue
    report "$f:$l [$rule]"
  done < <(hits "$@" | sort -u)
}

# 1. home-directory paths (placeholders such as /home/user are fine)
scan home-path '/(user|you|example|username|name|runner|ubuntu|vagrant|hpcsim)$' -E -e '(/home|/Users)/[A-Za-z0-9._-]+'

# 2. the local account name, unless it is a generic one
me=$(id -un 2>/dev/null || true)
case "$me" in
  ''|root|admin|ubuntu|user|runner|vagrant|hpcsim|test|docker) ;;
  *) [ "${#me}" -ge 4 ] && scan local-username '' -F -w -e "$me" ;;
esac

# 3. email addresses: not noreply, not example, not inside a URL, not an ssh remote
scan email '(noreply|@example\.|://|^git@|@(odata|Redfish)\.)' -E -e "[^[:space:]<>\"'(]*[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"

# 4. tokens, keys and credentials in URLs
scan secret '^https?://(user|username|you|me|x|example):' -E \
  -e 'gh[pousr]_[A-Za-z0-9]{20,}' -e 'github_pat_[A-Za-z0-9_]{20,}' -e 'glpat-[A-Za-z0-9_-]{20,}' \
  -e 'AKIA[0-9A-Z]{16}' -e 'xox[baprs]-[A-Za-z0-9-]{10,}' -e 'AIza[0-9A-Za-z_-]{35}' \
  -e '(^|[^A-Za-z0-9])sk-[A-Za-z0-9_-]{32,}' \
  -e 'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}' \
  -e '-----BEGIN [A-Z ]*PRIVATE KE[Y]-----' \
  -e 'https?://[^/[:space:]]*:[^@/[:space:]]+@'

# 5. patterns only this machine's owner knows (never committed)
deny="$(git rev-parse --git-dir)/privacy-deny"
if [ -f "$deny" ]; then
  while IFS= read -r re; do
    case "$re" in ''|'#'*) continue ;; esac
    scan local-deny '' -E -e "$re"
  done < "$deny"
fi

# Rules 6-8 look at files, not text: not for a commit message.
if [ "$mode" != --message ]; then
  # 6. local-only tooling and key material
  deny_paths='^(\.claude|graphify-out|compose/generated)/|(^|/)\.env(\.[^/]*)?$|\.(pem|key|p12|pfx|jks|kdbx)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)|(^|/)\.DS_Store$'
  for f in "${files[@]}"; do
    case "$f" in *.example|*.sample|*.template) continue ;; esac
    printf '%s' "$f" | grep -qE "$deny_paths" && report "$f [local-only-path]"
  done

  # 7. symlinks must stay inside the repo: an absolute or ../ target leaks a directory layout
  while read -r smode sha _ path; do
    [ "$smode" = 120000 ] || continue
    target=$(git cat-file blob "$sha")
    case "$target" in
      /*|../*|*/../*|..) report "$path [symlink-leaves-repo]" ;;
    esac
  done < <(git ls-files -s -- "${gfiles[@]}")
fi

# 8. commits are authored with the GitHub noreply address (the remote rejects private ones)
if [ "$mode" = --staged ] && [ "${PRIVACY_ALLOW_EMAIL:-}" != 1 ]; then
  case "$(git config user.email)" in
    *@users.noreply.github.com) ;;
    *) report "git config user.email [identity-not-noreply]" ;;
  esac
fi

if [ "$fail" -ne 0 ]; then
  cat >&2 <<'EOF'
Blocked: the lines above would publish personal data. Fix the file (placeholder, relative path, .gitignore),
or unstage it. Do not bypass the hook. A public third-party value that is safe goes in .privacy-allow.
EOF
  exit 1
fi
echo "privacy: ok"
