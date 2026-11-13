# ADR 0057: Two verification gates and impact-selected live tests

Status: accepted (issue 576; design by the owner, baseline measured 2026-11-13).

## Context
`./scripts/verify.sh --full` took 22 minutes 37 seconds and was required before every merge. The baseline per-module timing (`compose/generated/verify-times.json`, the `SLOW` line) showed where it went: the real Jira module 434 s (one 419 s setup, Jira's own startup and wizard), `test_slurmresilience_real` 216 s (90 s of it a wait that never could succeed), a Chromium module 97 s, and the other 15 isolated modules 35 to 60 s each. A one-line docs PR paid the same as a change to `stack.py`.

## Decision
- **V-1 Two gates.** Before merging a PR to `dev`: `verify.sh --changed`. Before any `dev` to `main` merge: `verify.sh --full --with-jira`. Nightly: the same, by a user systemd timer (`verify.sh --install-nightly [HH:MM]`, `--remove-nightly`; no sudo, no new top-level script).
- **V-2 Impact map.** `tests/real/impact.yaml` maps changed paths to live-test modules; `scripts/impact.py` reads it. Per changed file the first matching rule wins, the matches are unioned, `ALL` selects every module, `[]` selects none. A path no rule matches selects `ALL`, and so does an unreadable rules file or a git error. A changed `tests/real/test_X.py` selects itself. `tests/test_impact.py` fails when a live module is in no rule or a rule names a module that does not exist.
- **V-5 `validate.sh` runs beside the live tests** (its own log, its own timing); `--serial` restores the old order. `--trust-ci` skips it when the CI run for `HEAD` passed and the tree is clean.
- **V-6 Fail fast in `--changed`:** `--failed-first --maxfail=1`. `--full` runs everything and reports every failure.
- **V-7 Timing on every run:** per-module wall time (stack up and down included) to `compose/generated/verify-times.json` (last 20 runs) and a `SLOW` line.
- **V-8 No parallel live modules.** Stacks use fixed IPs and ports (ADR 0018) and 16 GB allows one stack.
- **Real Jira is optional.** `test_jira_dc_real` is left out of a plain `--full` and the default run unless `--with-jira` is given (owner decision). A change that names it (`benchctl/jira_real.py`, its own file) still runs it: `impact.py` prints `+test_jira_dc_real` after `ALL`. The summary always says `NOTE real Jira skipped (optional part)`; a skip is never silent. Pre-seeding Jira's database was rejected: it ties a snapshot to one Jira version and the 3-hour test licence counts from Jira's start.

## Consequences
- A docs or page PR runs no or few live modules; a stack change still runs everything.
- The impact map can miss a dependency; the nightly run and the release gate bound the damage to one day and never to `main`.
- `impact.yaml` must be kept current when a live module or a source module is added; the coverage test says which.
