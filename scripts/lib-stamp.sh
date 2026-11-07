#!/usr/bin/env bash
# A pass stamp for validate.sh, so verify.sh does not run it again on the same tree (issue 237).
# The key covers HEAD, every tracked change (staged or not), the content of untracked files (not ignored ones) and COV_FLOOR.
# Any edit, new file or commit changes the key, so a stale stamp can never skip a check. The stamp lives in .git (never committed).
stamp_file() { echo "$(git rev-parse --git-dir)/validate.stamp"; }
tree_key() {
  {
    git rev-parse HEAD
    git diff HEAD
    git ls-files -o --exclude-standard -z | xargs -0 -r git hash-object --
    echo "COV_FLOOR=${COV_FLOOR:-90}"
  } 2>/dev/null | sha256sum | cut -d' ' -f1
}
stamp_write() { printf '%s %s\n' "$(tree_key)" "$(date +%s)" >"$(stamp_file)"; }
stamp_fresh() {  # prints the age in seconds and returns 0 when the stamp matches the tree now
  local f key ts; f=$(stamp_file); [ -f "$f" ] || return 1
  read -r key ts <"$f" || return 1
  [ "$key" = "$(tree_key)" ] || return 1
  echo $(( $(date +%s) - ts ))
}
