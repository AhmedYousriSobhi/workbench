#!/usr/bin/env bash
# Read the pytest summary line of a step log: how many tests passed and how many were skipped (issue 818).
# A live step that skipped everything exercised nothing, so verify.sh must not print a plain PASS for it.
pytest_counts() {  # pytest_counts LOG: prints "PASSED SKIPPED" from the last line (0 when absent)
  local line p s; line=$(tail -1 "$1" 2>/dev/null)
  p=$(printf '%s' "$line" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+'); s=$(printf '%s' "$line" | grep -oE '[0-9]+ skipped' | grep -oE '[0-9]+')
  echo "${p:-0} ${s:-0}"
}
skip_word() {  # skip_word LOG: SKIP-ALL when tests were skipped and none passed, else PASS
  local p s; read -r p s < <(pytest_counts "$1")
  if [ "$s" -gt 0 ] && [ "$p" = 0 ]; then echo SKIP-ALL; else echo PASS; fi
}
skip_note() {  # skip_note LOG NAME: the NOTE or WARN line for a step with skips (nothing when none)
  local p s; read -r p s < <(pytest_counts "$1")
  if [ "$s" -gt 0 ] && [ "$p" = 0 ]; then echo "WARN  $2: every test was skipped, nothing was exercised (see SKIPPED lines)"
  elif [ "$s" -gt 0 ]; then echo "NOTE  $s tests skipped in $2 (see SKIPPED lines above)"; fi
}
