# ADR 0031: Governor actions run outside the state lock

Status: accepted (issue 342; closes the open item of issue 53)

## Context
`Governor.step()` held its lock while it ran the mitigation plan (docker pause, stop). `handle()` needs the same lock for every
control command, `status` included. A container stop takes up to 20 s, so under memory pressure, exactly when the page matters,
`status` waited for the whole plan and the page showed the governor as unreachable.

## Decision
- `step()` decides under the lock (sample, ladder update, which actions to run, `_prev`) and runs the actions after releasing it.
  The same holds for the actions of a late-appearing container (`_refresh`) and for manual `freeze|thaw|stop|start`.
- `_act()` takes the lock only for its bookkeeping (audit line, counters, `stopped`); the lock is an `RLock`, so a caller that
  already holds it still works.
- A second lock, `_exec_lock`, serialises the docker calls themselves: a manual `stop` and a running plan never interleave, as
  before. `status`, `admit` and the other commands never take it.
- Ladder order, hysteresis and the plan itself are unchanged; only where the lock is held differs. `step()` is still one thread,
  so plans do not overlap each other.

## Consequences
- `status` answers within a second while actions run (`test_status_answers_while_a_plan_with_slow_actions_runs`). Its audit
  list can show a level change before the actions of that level have finished: the action lines appear as each one starts.
- A manual command sent during a plan now waits for the plan's current action instead of blocking `status`.
- The root service's mitigation path is unchanged (GV-8): no new allocation happens in an action, only a lock acquisition.
