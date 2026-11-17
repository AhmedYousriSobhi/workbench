# ADR 0050: Config lab: A/B replay, comparison and verdict

Status: accepted (owner decisions of 2026-11-12). Issue 542; epic 557. Companion to ADR 0049.

## Decision

- **Shape-preserving downscale.** `lab.max_nodes` (default 24, max 64). Every (partition x node shape x topology leaf) group keeps at least one node; the rest is split in proportion to production counts. Partitions, QOS, accounts and topology levels are all kept. Multiple-slurmd mode stays out of v1 (issue 457).
- **Trace** is a `sacct` export (fields in the operator guide), sampled at the node ratio, stratified by account, QOS and partition. Each job is resubmitted with the mapped user, account, QOS, partition, TRES (typed GPUs included), nodes, time limit and relative submit time; its body is `sleep <scaled elapsed>; exit <code>` (timeouts sleep past the limit, OOM exits 137).
- **Names.** Production node names are kept as Slurm `NodeName` and hostname (compose service `lab-node-NNN`). Users map to `lab-u001...`, `--keep-users` keeps them.
- **Time dilation.** Default target 2 h per side (30 min to 6 h). Every time-based scheduling parameter (see `KEY_CLASS` time-scaled) is divided by the same factor in both runs and listed in the report.
- **Same faults, same seed.** An attached scenario runs with a fixed seed at the same dilated times in both runs.
- **Snapshot and restore.** Before run A save `StateSaveLocation` and a `mysqldump`; restore both before run B. Prometheus data carries a `lab_run` label instead of being restored.
- **Repeats.** One run per side by default; `--repeat N` (at most 5) reports median and range; single runs are labelled.
- **Concurrency cap** `lab.max_running_jobs` (default 50). If the sampled trace exceeds it the import proposes fewer lab nodes rather than throttling submissions, so both runs see the same load.
- **Pressure.** Replay and validation one-shots are class P5 and admitted only if the estimate (250 MB) fits (GV-9). L2 pauses the replay clock (pauses are recorded; over 5 % of run length marks the run not comparable); L3 stops it as "aborted under memory pressure".
- **Verdict** (thresholds in `sim.yaml lab.verdict`, printed in every report): any hard failure (validation error, lost job, an alert that fired in A and not in B, still-pending up more than 20 %, median wait up more than 20 % in a partition with enough samples) is "Do not ship"; a new cost or a 5 to 20 % regression is "Ship with changes"; at least one improvement over 5 % and nothing worse is "Ship"; everything within 5 % is "No effect". A minimum sample size per partition keeps small partitions from causing false failures.
- **Suggestions** are rule-based from a small catalogue (preemptions with requeue, QOS limit dominating pending, idle-but-free GPUs, alert flapping, fairshare spread), never generated; "apply suggestion and rerun" edits the candidate overlay and starts a new A/B.
- **Report:** `report.md` and `report.json` under `compose/generated/lab/NAME/reports/RUN/`; `benchctl export lab-report NAME RUN --anonymize` (issue 614) maps production node names to lab names.

## Consequences

Policy behaviour (priority, fairshare, QOS, preemption, topology placement, Kubernetes admission and quotas, alert rule timing, Slurm state compatibility) is decided by the real software. Job runtimes, GPU and network contention, hardware timing, pod runtime and slurmctld load at production scale are approximate and labelled so in every report. A ratio above 100x marks results indicative.

## Addendum (issue 551, downscale, mapping, time scaling)

- Group key is the tuple of a node's partitions, its shape and its topology leaf (a node in two partitions is one group, so it covers both). A shape is the node's `NodeName` line without names and state, plus its `gres.conf` types.
- Remaining nodes are split by largest remainder in proportion to production counts, capped at each group's size. More groups than `max_nodes` is refused with the needed number.
- Sampling keeps at least one job per (account, QOS, partition) stratum so every account and QOS is present for fairshare and QOS limits; the seed is fixed.
- The dilation factor is `max(1, trace span / target)`: a short trace is not stretched. The span runs from the first submit to the last end.
- Scaled: `PriorityDecayHalfLife`, `PriorityMaxAge`, `PreemptExemptTime`, `PriorityCalcPeriod`, `OverTimeLimit`, `MinJobAge`, `KillWait`, `SchedulerTimeSlice`, `InactiveLimit`, `WaitTime`, the duration options of `SchedulerParameters` (`bf_interval`, `bf_resolution`, `bf_window`, `bf_max_time`, `sched_interval`, `batch_sched_delay`), partition `MaxTime` and `DefaultTime`, QOS `GraceTime` and `MaxWall`, job runtimes and limits. Daemon and node timeouts (`SlurmctldTimeout`, `SlurmdTimeout`, `MessageTimeout`, health check and power timers) are lab-owned: they keep the workbench's own stable values. A scaled value is never below one second (one minute for the minute-unit keys).
- The memory the governor must admit for a run is `nodes x 13 MB + 250 MB` (`lab_slurm.admission_mb`); the apply command (issue 553) passes it to `admit`.

## Addendum (issue 554, the run: owner decisions of 2026-11-17)

The ten design questions of issue 554, in the owner's words (the issue's comments "Design pass and owner decisions" and "Owner answers"). "Built" means built by plan steps 1 and 2 (`benchctl/lab_run.py`, `benchctl/lab_snapshot.py`, `images/slurm-ctl/restore-state.sh`); everything else waits for steps 3 to 5 or for issue 863. Choices made while building that the owner has not decided are listed after the table, to confirm.

| # | Decision (owner) | Built? |
|---|---|---|
| 1 | The replay driver (250 MB cap) runs as an **unlabelled P5 one-shot container**, like `lab-validate`, that polls the pause and abort flags. A labelled P5 container would be frozen at L3 before it could abort; the login node (P2, 40 MB) is too small. | No (step 3) |
| 2 | **Yes, a thread.** The governor starts a run thread that only supervises flags and progress; the work is the one-shot driver container (decision 1). SPEC C-24 gets a wording note ("no new long-running process" means no new daemon; a supervising thread inside the governor is allowed). | No (step 4; the SPEC note with step 5) |
| 3 | Snapshot restore is **internal to `lab-run`**, not a socket verb. (So the issue 553 addendum's example verb `lab-snapshot-restore` is not made.) | Yes: `lab_snapshot.restore`, called by `lab_run.drive` |
| 4 | **Lab node containers are split into a child issue** (863); issue 554 does not build them. | Not here (issue 863) |
| 5 | **Rewrite to `lab-uNNN`.** The dump's users become `lab-u001...` in accounting, and Linux users with those names are created on the login and lab nodes. No production user name reaches the cluster. | Partly: the jobs file, the progress record, the run record and the collected accounting hold lab names only (checked; a refusal gives a count, never a name; free text such as an abort reason has production names replaced by `<user>`). Not yet: the dump rewrite in `apply lab` (a snapshot's database dump may still hold production names: it lives outside the report folder and is deleted when the run ends) and the Linux users |
| 6 | The run length is the existing **`--target-hours` / `lab.target_hours`**, not a new `--duration`. | Yes (in `jobs.json` and `run.json`; the CLI flag comes with step 5) |
| 7 | `run lab` without `--ab` runs **the candidate side only**. | Yes (the candidate-only path) |
| 8 | **Add a seed.** Recorded in the report; both sides of the A/B use it. | Partly: recorded in `jobs.json` and `run.json`, read by both sides; the scenario engine does not take a seed yet (step 3 or 4) |
| 9 | Answered **"Both"** (relabel rule and external label with a reload). Unclear how the two combine, so it is **not implemented yet**; to be clarified before the Prometheus part (step 4). | No: a TODO in `lab_run.py` |
| 10 | The page **may start and stop a run through the governor** (W-3 only forbids Docker and root on the page). | No (step 5 and issue 556) |

**Implementation choices of steps 1 and 2, not decided by the owner (to confirm):**

- The run always maps users, so `--keep-users` stays an option of `get lab` only (from decision 5).
- The seed also drives the sampling; its default is 1, the seed `get lab` samples with, so a default run replays the jobs the plan showed.
- A candidate-only run takes no snapshot and has no restore or compare.
- An aborted A/B run whose snapshot was taken collects the side in progress (best effort) and restores the snapshot. (The issue's acceptance says "cluster restored after abort"; whether that means the snapshot or `remove lab` is not stated.)
- The 5 % pause limit is checked per side, against one side's length.
- The snapshot is taken before `apply_baseline` (the issue's order), so the restore also reverts the baseline's QOS and account rows while the lab marker still says the baseline is applied; step 4 decides whether `apply_candidate` re-applies everything or the snapshot moves after `apply_baseline`.
- A trace shorter than the target is not stretched (`time_factor` is at least 1, as in the issue 551 addendum), so its run is shorter than `target_hours`; the run records both.
- The restore needs a new start step in the slurm-ctl image (`restore-state`, run before slurmctld; it does nothing without a restore tar), because nothing can be exec'd into the controller while slurmctld is stopped.
- An account, QOS, partition or TRES value equal to a production user name makes the jobs file refuse (decision 5), rather than being renamed.

How steps 1 and 2 work:

- **States.** A/B: `idle, snapshot, apply_baseline, replay_a, collect_a, restore, apply_candidate, replay_b, collect_b, compare, report, done`; candidate only: `idle, apply_candidate, replay_b, collect_b, report, done`; `aborted` from any state that has not ended. A pause (L2) is a flag on a replay state; its time adds to `paused_s` (and to that side's own count), and a side paused for over 5 % of the run length marks the run not comparable.
- **Files.** `compose/generated/lab/NAME/reports/RUN/` holds `run.json`, `progress.json` (ADR 0049's `{name, state, percent, submitted, running, pending, paused_s}` plus the run id, side and flags, written atomically), the frozen `jobs.json` (read-only, its sha256 in `run.json`, checked before each side) and `a/`, `b/` with each side's `sacct` export. A job keeps only key, lab user, account, QOS, partition, TRES, node count, scaled submit, run and limit times, kind and exit code (OOM exits 137).
- **Pairing.** The driver names each job by its key (`j0001`), so A and B pair by key: the restore makes run B reuse run A's job ids.
- **Snapshot.** `compose stop slurmctld` (it writes its state on the way down), `docker cp` the spool out, `mariadb-dump` the accounting database, start slurmctld. The JWT key is never saved: the entrypoint installs it at every start from the keys `up` makes (issue 465).
- **Restore.** Only after run A's accounting is in the report folder (the restore erases it). Stop slurmctld and slurmdbd, drop and reload the database, dump it again and compare, start slurmdbd, `docker cp` the tar into the stopped controller, start it. The image's `restore-state` (run before slurmctld in the container's command) empties the spool except the JWT key, extracts the tar, writes the sha256 of every file and removes the tar. The run is refused (aborted) when a file or the database differs. The database compare ignores `AUTO_INCREMENT=` counters, which a reload resets.
- **Abort.** An aborted A/B run whose snapshot was taken collects the side in progress (best effort) and restores the snapshot, so the cluster is back where it started.
- **Not exercised live.** The command sequence is unit-tested against a fake Docker and the `restore-state` script runs for real in a temporary folder; `tests/real/test_lab_run_real.py` is written and not yet run.
