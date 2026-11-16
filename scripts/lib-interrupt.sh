#!/usr/bin/env bash
# Ctrl+C, SIGTERM and SIGHUP for scripts that run long children (pytest, compose): stop the whole child tree, give pytest time
# to tear down (stop the stack it started), then run the script's normal exit cleanup (issue 819). Source it, then:
#   ir_trap CLEANUP   install the traps; the function CLEANUP runs once on every exit, signalled or not, and may set IR_DONE
#   ir_bg CMD...      start CMD (a program, not a function) in the background in its own session; sets IR_PID.
#                     Redirect the call like a command: ir_bg pytest >"$log" 2>&1
#   ir_wait PID       wait for it; returns its exit code and stops what it left behind in its session
#   ir_run CMD...     run CMD to the end under these rules and exit with its code (a script whose job is one command)
# Why a session of its own: a non-interactive bash starts `cmd &` with SIGINT ignored, and Python keeps an ignored SIGINT, so
# pytest never saw Ctrl+C, skipped its teardown and outlived the wrapper. Its own session also keeps the terminal's Ctrl+C from
# reaching it twice; the trap forwards exactly one SIGINT. Only the sessions started here are signalled, nothing else.
# A first signal: SIGINT to each child session, up to IR_GRACE seconds (45) for it to end, then SIGTERM, then SIGKILL.
# A second signal skips the wait and kills the sessions at once. Exit code 130 (INT), 143 (TERM), 129 (HUP).
IR_GRACE=${IR_GRACE:-45}
IR_PID="" IR_DONE="" IR_SIG=""
_ir_groups=() _ir_t=0 _ir_code=0 _ir_how="" _ir_cleanup=":"

_ir_alive() {  # any of our sessions still has a process (kill -0 on a group: no signal is sent)
  local g; for g in "${_ir_groups[@]}"; do kill -0 -- "-$g" 2>/dev/null && return 0; done; return 1
}
_ir_kill() { local g; for g in "${_ir_groups[@]}"; do kill -s "$1" -- "-$g" 2>/dev/null; done; return 0; }
_ir_wait_gone() {  # _ir_wait_gone SECONDS: _ir_t is how long it took; returns 1 when something is still there.
  # No $(...) here: a signal to this shell would wait for a command substitution to finish, and a second one must not wait.
  _ir_t=0; while _ir_alive && [ "$_ir_t" -lt "$1" ]; do sleep 1; _ir_t=$((_ir_t + 1)); done; ! _ir_alive
}
_ir_stop() {  # INT, a bounded wait, TERM, KILL: what an operator would do by hand
  _ir_kill INT
  if _ir_wait_gone "$IR_GRACE"; then _ir_how="the child processes ended after ${_ir_t}s"; return; fi
  _ir_kill TERM
  if _ir_wait_gone 5; then _ir_how="the child processes did not end in ${IR_GRACE}s and were terminated"; return; fi
  _ir_kill KILL; _ir_how="the child processes did not end in ${IR_GRACE}s and were killed"
}
_ir_signal() {  # _ir_signal NAME CODE; bash runs this again, nested, when a second signal arrives during the wait
  if [ -n "$IR_SIG" ]; then _ir_kill KILL; _ir_how="the child processes were killed at once (second signal)"; exit "$_ir_code"; fi
  IR_SIG=$1 _ir_code=$2
  if _ir_alive; then
    echo "STOP  $1: stopping the child processes (up to ${IR_GRACE}s for pytest to tear down; Ctrl+C again kills them now)" >&2
    _ir_stop
  else _ir_how="no child process was running"; fi
  exit "$_ir_code"
}
_ir_exit() {
  local rc=$?
  trap 'echo "STOP  cleaning up, one moment" >&2' INT TERM HUP  # a foreground cleanup command still gets the terminal's Ctrl+C
  # a normal exit (or set -e) with a child still running: it is ours and must not outlive us
  if _ir_alive; then _ir_kill TERM; _ir_wait_gone 5 || _ir_kill KILL; fi
  "$_ir_cleanup"
  if [ -n "$IR_SIG" ]; then
    echo "STOPPED  by $IR_SIG: ${_ir_how}${IR_DONE:+; $IR_DONE}. Exit $_ir_code." >&2
    exit "$_ir_code"
  fi
  exit "$rc"
}

ir_trap() {
  _ir_cleanup=${1:-:}
  trap '_ir_signal SIGINT 130' INT
  trap '_ir_signal SIGTERM 143' TERM
  trap '_ir_signal SIGHUP 129' HUP
  trap _ir_exit EXIT
}

ir_bg() {
  # `trap -` gives the child a default SIGINT again; setsid makes it the leader of a new session and process group, so $! is
  # the group to signal. IR_MANAGED tells a nested ir_run that this tree is already looked after.
  ( trap - INT QUIT; export IR_MANAGED=1; exec setsid "$@" ) &
  IR_PID=$!; _ir_groups+=("$IR_PID")
}

ir_wait() {
  # A finished session is forgotten, so a later signal can never reach a new process that reuses its number; a stray it left
  # behind is in our tree and is stopped first.
  local rc=0 g t=0 keep=()
  wait "$1" || rc=$?
  kill -s TERM -- "-$1" 2>/dev/null
  while kill -0 -- "-$1" 2>/dev/null && [ "$t" -lt 50 ]; do sleep 0.1; t=$((t + 1)); done
  kill -s KILL -- "-$1" 2>/dev/null
  for g in "${_ir_groups[@]}"; do [ "$g" = "$1" ] || keep+=("$g"); done
  _ir_groups=("${keep[@]}")
  return "$rc"
}

ir_run() {
  [ -n "${IR_MANAGED:-}" ] && exec "$@"  # started by a script using this helper: stay in its session, it does the stopping
  [ "$_ir_cleanup" = ":" ] && ir_trap :
  local rc=0
  ir_bg "$@"; ir_wait "$IR_PID" || rc=$?
  exit "$rc"
}
