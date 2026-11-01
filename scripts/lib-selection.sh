#!/usr/bin/env bash
# Keep the user's workbench selection (node counts, parts, BMC nodes) across a script that builds and forgets its own
# (the real tests do). Source it, call selection_save first and selection_restore on exit (verify.sh traps it).
# The files are relative to the repository root; SELECTION_FILES and SELECTION_DIR can be overridden for a test.
SELECTION_FILES=${SELECTION_FILES:-"compose/generated/workbench.yaml compose/generated/profile compose/generated/sim.yaml"}
selection_kept=""

selection_save() {
  SELECTION_DIR=$(mktemp -d); selection_kept=""
  local f
  for f in $SELECTION_FILES; do
    [ -f "$f" ] && cp -p "$f" "$SELECTION_DIR/$(basename "$f")" && selection_kept="$selection_kept $(basename "$f")"
  done
  return 0
}

selection_restore() {  # a file that did not exist before is removed again
  [ -n "${SELECTION_DIR:-}" ] || return 0
  local f b
  for f in $SELECTION_FILES; do
    b=$(basename "$f")
    if [ -f "$SELECTION_DIR/$b" ]; then mkdir -p "$(dirname "$f")" && cp -p "$SELECTION_DIR/$b" "$f"; else rm -f "$f"; fi
  done
  rm -rf "$SELECTION_DIR"; SELECTION_DIR=""
}
