# ADR 0027: The memory plan admits against measured use, not container caps

Status: accepted (issue 287; builds on ADR 0010 and the governor admission, SPEC GV-9)

## Context
`benchctl plan` and `up` admitted a selection by the sum of its containers' memory limits. A limit is a cap the kernel
enforces; it is generous so a component never hits it. For a selection that really used 3.0 GiB the plan said 5.1 to 5.8 GB and
refused starts that fit. One component, the real Jira, is more than 90 percent of what the stack uses, so a wrong number there
decides everything. The app under test (the client being tested, 1 GB in the architecture doc) was in no number at all.

## Decision
| Decision | Why |
|---|---|
| Each component has the largest working set seen in a live run (`benchctl/memory.py`, `MEASURED_MB`, with date and setup) | Real numbers beat guesses; the table is in the architecture doc and a test fails when a component has none |
| The plan expects measured + 15 percent, never above the cap; an unmeasured component is expected at its cap | A small, explicit tolerance; unknown things stay conservative |
| Caps stay as they are and the governor still enforces them; `plan` prints expected and cap | Safety does not change, only the forecast |
| The app under test is a reserve (4 GB at first, a guess; 1 GB since issue 301) in `need` (`--app-mb N`, `WORKBENCH_APP_MB`; 0 when it already runs) | Its memory is real and competes for the same RAM; when it already runs, `MemAvailable` already excludes it, so counting it twice would be wrong |
| Profile `budget_gb` is the expected use of what is built today; the pre-flight adds the app reserve | One method for the profiles and for selections |
| The real Jira cap went from 3072 to 2560 MB (heap 768 MB), then to 2176 MB with a trimmed JVM (Serial GC, C1-only, 512 KB stacks; issue 289) | Smallest limit that passed setup and a ticket run twice (peak about 2040 MiB); without the flags 2560 MB was the floor |

## Consequences
- Selections that fit no longer get refused because caps are large; the verdict names the app reserve and how to leave it out.
- A new component must be measured once (docker stats on a running selection) and added to `MEASURED_MB`; until then the plan counts its cap.
- The governor's own admit (GV-9) is asked for the expected size of what is not yet running, not for the caps.
- Not measured yet: scenario-engine, user-sim, ticket-bot (not built), Kubernetes and the fidelity VM (not built). Teleport is at its cap and should get a higher one with the next measurement.

## Addendum: the app reserve is measured, not guessed (issue 301)

`benchctl measure app --match NAME [--pid N] --save` samples the PSS of the app's processes and saves the suggested reserve (peak + 10%, rounded up to 100 MB) in `compose/generated/app_reserve.json` (gitignored). `memory.app_reserve()` resolves it: `--app-mb` (sets the env), then `WORKBENCH_APP_MB`, then the saved file, then APP_MB. The plan says which one it used (`given`, `measured on DATE`, `assumed, not measured`). One real session (a desktop client with one cluster, a terminal, the real Jira, every view opened during the `client-stress` scenario) peaked at 664 MB (469 MB idle, 8 processes), so APP_MB went from 4096 to 1024 (the peak plus room for a second cluster or terminal). Method and result: `docs/guides/memory.md`. A saved measurement still wins over the default.
