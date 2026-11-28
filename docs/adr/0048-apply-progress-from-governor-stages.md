# ADR 0048: Apply progress comes from the governor's stages, with a job id

Status: accepted. Part of the UI redesign (epic 491); review item U5; issue 519. Amends ADR 0017 (the page no longer waits for an audit line).

## Context

The page decided that an apply had finished by matching the governor's audit text and waiting up to 10 minutes. Log wording had become an API.

## Decision

- `benchctl up` prints one `@stage {json}` line per change (`{"plan": [names]}`, then `{"stage": name, "state": "running"|"done"}`) only when `WORKBENCH_STAGE_LINES=1`. The governor sets it; a person at a terminal sees no machine lines. The stages are Build images, each start stage of `stack.stage_services`, and Restart slurmctld when it happens.
- The governor runs changes through a streaming runner (`streamer` beside `effector`), parses only `@stage` lines (anything else is ignored) and keeps the latest change as `status.change`: `{job, label, state: running|done|failed, stages: [{name, state: waiting|running|done|failed, secs}], started, finished}`. The value is replaced whole on each update, so `status` never reads a half-written one. When a change fails, the stage that was running is marked failed.
- `apply-plan` (and every change command) returns `{"started": true, "job": N}`. The page keeps that job id and reads `status.change` for it; a different or missing job is reported as not reported, never guessed. A governor that returns no job (an older one) gets the old message and a pointer to Governor activity.
- The Cluster architecture sheet shows the stages as a Progress list and one summary line. Polling stops while the tab is hidden (W-11).

## Consequences

Only the latest change is kept (one change runs at a time under the change lock). The audit lines are unchanged. Stage seconds are measured by the governor, not by the page. A failure's reason is still in Governor activity: the stage names where it stopped.

## Addendum (issue 986): queued changes and the last ten

A second change waits behind the running one (the change lock, issue 341), and the page and `benchctl` send changes back to back (Clear is `on` then `resume`; scenario daemon steps), so queuing stays. But `status.change` used to switch to the newest job at once: while a plan was applying, a Clear on a node made `change` show the Clear job with no stages, the plan's stage lines were dropped, and after 30 s the plan sheet said the governor no longer reported its job.

- `status.change` is the running change (else the last one), as before, so an older page still works.
- `status.changes` lists every kept change, oldest first, in the same shape plus the state `queued` (with `started: null` until it runs): all unfinished ones and the last 10 finished.
- The page and `benchctl` (`checks.governor_job`) look their job up in `changes` first, then in `change`. A queued job reads "waiting for the change before it to finish"; a job that ended while another started is still found.
- A lab run or validation is refused while a change runs or waits.
