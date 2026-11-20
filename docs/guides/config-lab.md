# Config lab: import exported production configuration

**In 30 seconds:** copy files you exported from a production cluster into a folder, then `benchctl import lab NAME --from DIR`. The files are parsed, secrets are removed, versions are recorded, and the result is stored under `compose/generated/lab/NAME/` (gitignored). Nothing connects to production.

This is step one of the config lab (goal G6, [ADR 0049](../adr/0049-config-lab-scope-and-overlay-model.md), [ADR 0050](../adr/0050-config-lab-ab-replay-method.md), epic 557). Diff, apply and replay come with later issues; this page says what exists now: import, the downscale plan and validation of the imported configuration.

## Do it

On the production side, export read-only files into one folder (any of these; the importer takes what is there):

```bash
mkdir export && cd export
cp /etc/slurm/slurm.conf /etc/slurm/topology.conf /etc/slurm/gres.conf .
sacctmgr dump CLUSTER file=sacctmgr.dump
sacctmgr -P show qos > qos.txt
sacct -a -X -P -S 2026-01-01 -E 2026-01-08 \
  -o JobID,User,Account,QOS,Partition,AllocTRES,ReqTRES,NNodes,Timelimit,Submit,Start,End,Elapsed,State,ExitCode > trace.sacct
mkdir k8s rules teleport      # then put policy manifests, Prometheus rule files and Teleport roles in them
printf 'slurm: 25.11.9\nkubernetes: v1.36.1\nprometheus: 2.53.0\nteleport: 18.11.8\n' > versions.yaml
```

Copy the folder to this machine and import it:

```bash
./scripts/benchctl import lab prod-a --from ./export
```

It prints what was stored, what was scrubbed, dropped or ignored, the versions and the list of what is not tested. Run it again with `--replace` to overwrite a lab of the same name. Lab names are lowercase letters, digits and `-`.

## See how it will be downscaled

```bash
./scripts/benchctl get lab prod-a                     # nodes per group, sampled jobs, user mapping, time dilation, key classes, memory
./scripts/benchctl get lab prod-a --max-nodes 32 --keep-users --target-hours 3 -o json
```

Changes nothing. It shows:

- **Nodes.** `lab.max_nodes` (default 24, at most 64). Set it in `sim.yaml` (the commented `lab:` block), or per call with `--max-nodes`.
  - Every (partition x node shape x topology leaf) group keeps at least one node. The rest is split in proportion to the production counts.
  - If there are more groups than the budget, the command says so and how many nodes it needs. Above 64 groups, drop a partition, shape or switch from the export.
  - Production node names are kept as the Slurm `NodeName` and hostname. The compose services are `lab-node-001`, `lab-node-002`, ...
  - A ratio above 100 production nodes per lab node marks the results indicative.
- **Jobs.** The trace is sampled at the same ratio (a lab node's share of the production nodes), per (account, QOS, partition) stratum, at least one job each, the same jobs every time. Users become `lab-u001`, `lab-u002`, ... (see "Users") unless `--keep-users`. If the sampled trace would run more than `lab.max_running_jobs` (default 50) at once, a smaller node count is proposed instead of throttling, so both runs see the same load.
- **Time.** The dilation factor is the trace length divided by `--target-hours` (default `lab.target_hours`, 2; 0.5 to 6), never below 1. Every time-based scheduling parameter (listed, with before and after) is divided by it, and so are partition time limits and job runtimes, the same in both runs.
- **Key classes.** `slurm.conf` keys are `policy` (applied live), `time-scaled`, `restart` (applied, slurmctld restarts), `lab-owned` (hosts, paths, auth, privileged plugins: the base value stays, reported "not tested") or `unknown` (applied and flagged, restarts slurmctld). A key ending in `Type` also restarts.
- **Memory.** `lab nodes x 13 MB + 250 MB` for the replay is the amount a lab run asks the governor to admit.

## Check it with the real tools

```bash
./scripts/benchctl validate lab prod-a          # needs the governor (./scripts/benchctl start); baseline = what you imported
./scripts/benchctl validate lab prod-a -o json  # the same as {tool, command, ok, messages, restart_required} per tool
```

Question it answers: does production's configuration run in the lab, and which keys does the lab replace? The imported `slurm.conf` is first rendered for the lab (`compose/generated/lab/prod-a/render/`: the downscaled nodes, hosts, paths and auth keys replaced by the workbench's own, time-scaled keys divided by the dilation factor), then:

| Line | What runs | PASS means |
|---|---|---|
| `slurm.conf` | a throwaway slurmctld on the rendered files (its own `--internal` network with no route to the live stack or the internet, removed after the run; empty state, its own munge key) | it logged `Running as primary controller` within 20 s, which it does only after it has checked the nodes, partitions and plugins. The first `fatal:` line fails and is shown; `error:` lines are warnings (unresolvable node names and an unreachable slurmdbd are expected). A partition `QOS=` is not checked here (the one-shot has no accounting); `sacctmgr load` covers the QOS |
| `sacctmgr load` | a throwaway slurmdbd on a scratch database of the running MariaDB; QOS added (a `GraceTime` in seconds, the `gres/` and `license/` TRES they use declared through a short-lived slurmctld), the dump loaded, read back and compared | everything loaded; a changed option is a warning. `SKIP` when the database user lacks the rights slurmdbd needs (see below) |
| `promtool check rules`, `promtool test rules` | promtool from the pinned Prometheus image | rules parse; the `*.test.yml` cases pass |
| `kubectl dry-run` | `kubectl apply --dry-run=server` against the lab's API server | the API server accepts every kept object |
| `teleport role` | structure check (kind, version, allow/deny, misspelled keys); with Teleport running also a temporary role created and deleted | "structure only" or "accepted by Teleport" |
| `secret scan`, `version check` | the import's secret rules over the stored and rendered files; the version match | none left; a differing version is "indicative" |

A tool whose part is not running (accounting, Kubernetes, Teleport) is a `SKIP` line with the command to start it, and the summary says `passed with N skipped`, never just `passed`. Exit codes: 0 passed, 1 failed or refused, 3 `--side candidate` (comes with issue 553; one line, not a failure). A line marked `[restart required]` means applying that configuration restarts slurmctld.

It runs through the governor (`lab-validate`), one run at a time, and only when the memory level is L0 or L1. Otherwise one message says why (`refused at L2: ...`).

- The one-shots are class P5 and have no `workbench.class` label.
- They are removed afterwards, and the scratch database is dropped.
- Each run first drops any `lab_validate_*` database an interrupted run left.

**Accounting rights.** Every time it runs, `benchctl up` gives the database user `slurm` the rights slurmdbd needs:
- Tables: create, drop, alter, index, select, insert, update, delete, lock tables, create temporary tables.
- Routines: create routine, alter routine and execute. slurmdbd creates a stored function (`get_lineage`) when it builds its tables, and fails without them.
- The rights are on databases named `lab_validate_*` only, never on the real accounting database.
- They come without grant option.
- An existing stack needs no restart.
- Without the routine rights the accounting check reports `skipped: slurmdbd needs CREATE ROUTINE and ALTER ROUTINE` instead of failing.

## What is read

| File | Content |
|---|---|
| `slurm.conf`, `topology.conf`, `gres.conf` | scheduler keys, nodes, partitions, switches, GPU types |
| `sacctmgr.dump`, `qos.txt` | accounts, users, fairshare, QOS |
| `trace.sacct` | `sacct --parsable2` export; needs JobID, User, Account, Partition, Submit, Elapsed, State and AllocTRES or ReqTRES; step rows (`123.batch`) are dropped |
| `k8s/*.yaml` | only Namespace, DeviceClass, ResourceClaimTemplate, PriorityClass, ResourceQuota, LimitRange, PodDisruptionBudget, FlowSchema, PriorityLevelConfiguration, Role, ClusterRole, RoleBinding, ClusterRoleBinding; every other kind (Secrets, workloads, CRDs) is dropped and counted |
| `rules/*.yml` | Prometheus rule files; `NAME.test.yml` is a `promtool test rules` file |
| `teleport/*.yaml` | `kind: role` only; tokens and other kinds are dropped |
| `versions.yaml` | `slurm`, `kubernetes`, `prometheus`, `teleport` (a version you leave out is "not given") |

Not imported, and listed in every report as not tested: `cgroup.conf`, `job_submit.lua`, prolog and epilog scripts, Grafana dashboards, Kubernetes workloads and controllers, Secrets, admission webhooks, unknown CRDs, Alertmanager routing.

## Edit, diff, apply, remove

Apply and remove go **only through the governor** (it must run: `./scripts/benchctl start`), at levels L0 and L1, one change at a time, each audit-logged. At L2 or higher you get one message: `refused at L2: stack changes are paused (GV-8)`. The cluster must be running (`./scripts/benchctl up`).

```bash
./scripts/benchctl add candidate prod-a        # copy the baseline to compose/generated/lab/prod-a/candidate/
$EDITOR compose/generated/lab/prod-a/candidate/slurm.conf
./scripts/benchctl diff lab prod-a             # what changes, and whether slurmctld restarts
./scripts/benchctl apply lab prod-a            # the baseline, as imported
./scripts/benchctl apply lab prod-a --side candidate
./scripts/benchctl remove lab prod-a           # back to the plain sim.yaml cluster
```

The candidate holds the same file names as the baseline; a file you delete from `candidate/` means "as the baseline". Effective configuration is the generated base, then the baseline, then the candidate.

**What apply puts on the cluster**

| Part | How | Undone by remove |
|---|---|---|
| `slurm.conf` keys of class policy, time-scaled, restart | written over the generated file (the old value stays in a trailing `# lab-was` comment), then `scontrol reconfigure`, or a slurmctld restart when a changed key is restart-class | strips the overlay, reconfigures or restarts, compares the keys a lab can change with the saved pre-lab `scontrol show config` |
| QOS and accounts | `sacctmgr add/modify qos`, `sacctmgr load` (the production cluster name is replaced by `workbench`); `sacctmgr dump` before and after are saved in `render/` | reloads the pre-lab dump (puts changed rows back), deletes only rows that are in the after dump and not in the before dump, restores changed QOS limits |
| Prometheus rules | files `NAME-FILE.yml` in `compose/generated/prometheus/rules/lab/` (a glob in `prometheus.yml`), SIGHUP reload, then `prometheus_config_last_reload_successful` must be 1 | deletes the files, reloads |
| Kubernetes policy objects | `kubectl apply` with the label `workbench.lab=NAME` | `kubectl delete` by that label |
| Teleport roles | `tctl create -f`, label `workbench.lab=NAME` | `tctl rm role/X` for the roles it made |

Not applied: keys in no class (listed as `not applied: unknown`), lab-owned keys (the base value stays, "not tested"), and node and partition structure (that comes with the run). Times are **unscaled** (`time_scale` 1.0): a later run scales them. An imported QOS or account that names a partition the lab does not have gives a warning.

An object that already exists without the lab's label (for example a PriorityClass of the fixtures, or a Teleport role of the same name) makes apply refuse with one message naming it, before anything changes.

**The marker.** `compose/generated/lab/active.json` says which lab is on the cluster: name, side, applied keys, `time_scale`, what it created.
- The governor `status` carries it as `lab`.
- The page shows "Lab NAME applied" in the top strip, so nobody tests the plain cluster by mistake.
- `benchctl up` and `scale` regenerate `slurm.conf` and put the lab's keys back, with one line: `lab NAME is active: its settings were re-applied`.
- `benchctl down` keeps the marker, so a restarted cluster still has the lab.
- Only `benchctl remove lab NAME` ends it.

**If remove refuses.** A lab that made objects in accounting, Kubernetes or Teleport needs that part running when you remove it. Otherwise remove refuses with one message and the lab stays active. Start that part and run it again.

Rows `sacctmgr` refuses to delete are listed.

## Run the replay (built, not yet usable end to end)

```bash
./scripts/benchctl run lab prod-a --ab                  # baseline, restore, candidate; returns once the run started
./scripts/benchctl run lab prod-a                       # the candidate side only
./scripts/benchctl run lab prod-a --ab --target-hours 1 --seed 7
./scripts/benchctl run lab prod-a --ab --repeat 3       # the same jobs three times; the report says whether the verdicts agree
./scripts/benchctl run lab prod-a --ab --scenario lab-node-loss   # the same Slurm faults at the same lab times on both sides
./scripts/benchctl status                               # one line: state, percent, submitted, running, pending, paused
./scripts/benchctl stop lab prod-a                      # end the run (an A/B run is put back to its snapshot)
```

**Built, not yet run live.** The lab nodes (issue 863) and the `lab-uNNN` users (issue 869) exist in code and unit tests; the live tests (`./scripts/verify.sh --unit lab_nodes --clean`, `--unit lab_users --clean`) have not been run, so treat a first replay as the live check ([ADR 0050 addendum](../adr/0050-config-lab-ab-replay-method.md)).

**Lab nodes (issue 863).** `apply lab` on a lab that has a trace starts the downscaled nodes.
- One container per lab node (`lab-node-001`, ...), a copy of a simulated compute node: unprivileged, memory-limited, class P4. Its hostname is the production node name, and it has the `lab-uNNN` users.
- The lab block of `slurm.conf` gets one `NodeName` line per node and the production partitions, listing only the nodes kept. `Gres` becomes a file-less `gres.conf` line. `AllowGroups` and `DenyGroups` are dropped, because the production groups do not exist here.
- A simulated partition with the same name as a lab partition is switched off while the lab is active; if a lab partition is the default, the simulated ones lose `Default=YES`. `remove lab` puts both back and removes the containers.
- slurmctld restarts when the node set changes. A lab node with the name of a simulated node is refused with one message.
- Topology is not rendered: the nodes have no switch tree (`not tested`). The node count is `lab.max_nodes` from `sim.yaml`; a `--max-nodes` given to `run lab` must match it.
 Each lab keeps one mapping from production user to `lab-uNNN`.
- It lives in `compose/generated/lab/NAME/users.json` (mode 600), never in a report, `status`, export or diag bundle.
- It covers the dump's users and the trace's users, is numbered once by sorted name and then only grows: a new name goes at the end, a number is never reused. The same person is the same `lab-uNNN` whatever the seed or `--target-hours`.
- `import lab NAME --replace` keeps it, and is refused while a run of that lab is active or recovering.
- `apply lab` rewrites the dump (users, coordinators, names in the description) before `sacctmgr load`, and creates the Linux users (uid 30000+N, no home) on slurmctld, slurmdbd and the login node. `remove lab` deletes them.
- A trace user with no account in the dump gets a number too; its jobs are refused by Slurm and counted as `rejected: no association`.

**The report (issue 555).** `report.md` and `report.json` come from each side's accounting, jobs paired by key.
- Per partition and QOS: jobs, still pending, median and p95 wait, shown in production-equivalent time (lab time times the dilation factor). Also preemptions and GPU-hours lost.
- The verdict follows ADR 0050: a hard failure is "Do not ship" (a lost job, still-pending up over 20 %, or a median wait up over 20 % in a partition with enough samples). A new cost or a 5 to 20 % regression is "Ship with changes". An improvement over 5 % and nothing worse is "Ship". Within 5 % is "No effect".
- A partition with fewer than `min_samples` started jobs never fails a run. A run that is not comparable has no verdict, and a candidate-only run has none either.
- The thresholds are `lab.verdict` in `sim.yaml` (defaults in the commented block) and are printed in every report, with the time-scaled parameters. One run per side is labelled a single run.
- Suggestions come from a fixed catalogue (preemptions, wait spread between accounts, pending up, wait regression, a QOS limit holding most jobs back, a flapping alert). They are text only.
- Not measured yet, and listed in every report: Kubernetes counts, idle-but-free GPUs, fairshare usage spread. Alerts, flapping, scheduler load and pending reasons are listed only when they could not be read.
- **Pending reasons (issue 555).** A table of sacct's `Reason` per side.
  - Slurm keeps there the last reason a job was held back *other than Priority or Resources*, even after the job ran. The row "(only Priority or Resources)" counts the jobs that only waited their turn.
  - When more than half of the candidate's held-back jobs name a QOS limit (`QOS...`), the report suggests reviewing the QOS limits.
  - A run collected before this column existed lists the mix as not measured.
- **Scheduler load (issue 555).** slurmctld's own gauges over each side's replay (the `lab_run`-labelled series), each as max and median of the samples.
  - Measures: last main and backfill cycle (microseconds), both queue lengths, server threads, the agent queue, messages queued for slurmdbd.
  - Real controller numbers: never time-scaled, never part of the verdict. The lab controller runs far fewer nodes than production, so compare A with B; do not read them as production load.
  - If Prometheus has none of the series, the report says not measured.
- **Alerts (issue 555).** The report lists the alerts Prometheus saw firing on each side, with the time to the first one (production-equivalent) and the flaps. If Prometheus does not answer, they stay under "not measured".
- **Repeats (issue 555).** `benchctl run lab NAME --ab --repeat 3` runs the same jobs three times in a row (same seed, one run after the other; the command stays until the last run ends and stops if one aborts). The report then says "repeated: 2 runs" and whether the verdicts agree. One run is always labelled single-run.
- **QOS (issue 555).** The report has a wait-by-QOS table next to the one by partition.
- **Export (issue 614).** `benchctl export lab-report NAME [RUN] [--anonymize] [--json] [-o FILE]` writes one run's report (`RUN` is a run folder or `last`). `--anonymize` swaps production node names for `lab-node-NNN` and user names for `<user>` in the text and the JSON; the mapping is never written. The MCP tool is `lab_report_export`.

A run goes **only through the governor** (`./scripts/benchctl start`), needs the cluster with accounting (`benchctl up --with accounting`) and a lab with a trace. One run at a time.

- **Start.** Only at L0 or L1. The governor answers at once (`preparing`), freezes the run's jobs, then asks for `lab nodes x 13 MB + 250 MB` (the same estimate `get lab` prints). `run lab` waits for the outcome and prints one line: the run started, or why it was refused, for example `refused: 302 MB would leave ...`. `benchctl status` keeps a refusal until the next start.
- **While it runs** (and while it is preparing or being recovered): `up`, `scale`, `apply-plan`, a profile change, the other lab commands, `remove jobs` and `import lab --replace` are refused with one message naming `benchctl stop lab NAME`. Freezing, thawing or draining a node stays allowed.
- **`down` and `stop`** are refused too (exit 3), naming two ways out: `benchctl stop lab NAME`, wait until `benchctl status` shows it ended, then `down`; or `benchctl down --force` (also `stop --force`), which aborts the run, skips its restore and keeps the snapshot (`benchctl remove lab NAME` then returns to the plain cluster). `--yes` does not override; with `BENCHCTL_AGENT=1`, `--force` needs `--yes` too.
- **A fault or a scenario during a replay** is allowed but recorded in `reports/RUN/interference.jsonl`. The run is then not comparable, and `status` names the first action and its time.
- **A scenario in the run** (`--scenario NAME`, a file in `scenarios/`) is part of the experiment, not interference: both sides play it at the same lab times, so its steps do not mark the run not comparable. Details below.
- **If the governor restarts** during a run, its status says `recovering` until the run is wound up: the run is aborted and, for an A/B run, its snapshot restored once memory is calm. The same commands are refused meanwhile.
- **L2** pauses the replay: no new job is submitted and the pause is counted. A side paused for more than 5 % of the run length marks the run not comparable. L1 does not pause it.
- **L3** aborts it as `aborted under memory pressure (L3)`. The jobs it submitted are cancelled. An A/B run puts the cluster back to its snapshot once the level is back at L0 or L1 (status says `restore waits for L0-L1` meanwhile).
- **`stop lab NAME`** works at any level and ends the run the same way.

What happens, in order. With `--ab`: snapshot, apply baseline, replay A, collect A, restore, apply candidate, replay B, collect B, compare, report. Without it: the candidate only (apply, replay, collect, report). Any state can end in `aborted`.

- **The plan of a run.** A run freezes one `jobs.json` that both sides read: the sampled jobs with lab users `lab-u001`, `lab-u002`, ... (never the production names, whatever `get lab --keep-users` showed), scaled submit, run and limit times, the seed (default 1, the one `get lab` samples with) and the run length from `--target-hours` or `lab.target_hours` (a trace shorter than that is not stretched).
- **The replay.** A one-shot container (P5, 250 MB, no `workbench.class` label, so the governor never freezes it before it can stop) submits each job at its lab time as its `lab-uNNN` user.
- **A job** sleeps for its scaled run time, then exits with its exit code. A timeout sleeps past its limit; an out-of-memory job exits 137. Its name is its key (`j0001`), so A and B are paired by key.
- **Snapshot and restore.** Before run A the slurmctld state and the accounting database are saved; before run B both are put back and compared file by file, and run A's accounting is written to the report folder first (the restore erases it).
- **Prometheus.** While a side replays, slurmctld's metrics are also scraped by the `lab-run` job with a `lab_run="NAME/RUN/SIDE"` label, so the two sides can be told apart in Grafana. Only those series carry it. A cluster started before this change needs one Prometheus restart (`benchctl up`) to learn the job.

**A scenario in the run (`--scenario`, issue 554).** `benchctl run lab NAME --ab --scenario lab-node-loss` plays a scenario's faults on the lab nodes during each side.
- **Times.** A scenario's `at:` is production time. It plays at `at` divided by the run's time factor, on the replay's own clock, so both sides see each step at the same moment of the replay. At L2 that clock stops with the replay: no step runs and no expectation is checked until the level drops.
- **Nodes.** `first cpu node`, `second gpu node` and the other selectors pick the lab's nodes (cpu: without GPUs, gpu: with), not the simulated ones. Reports name them by their Slurm (production) name; `export lab-report --anonymize` maps them.
- **What can play.** Only `slurm` steps on lab nodes: `drain`, `down`, `resume`, `power_off`, `power_on`, `slurmd_stop`, `slurmd_start`, `slurmd_restart`, and `slurmctld_restart`. The BMC farm, BCM, GPU exporter and the other parts know only the simulated nodes. Expectations: `slurm.node_state`, `slurm.job_state`, `slurm.stepd`. `scenarios/lab-node-loss.yaml` (SC-59) is written for a lab run.
- **Refused at the start**, with one message per problem (no run folder is left): another target or step, a node that is not a lab node, a scenario longer than a side, and two steps on one node that the time factor puts under 1 s apart (the message names the gap the scenario needs).
- **Cleanup.** After each side (before the A to B restore) and after an abort, once memory is calm, the run unpauses the containers it powered off, lets slurmd start again where it stopped it, and resumes every node it named. The snapshot alone would not unpause a container.
- **Report.** A *Scenario* section lists the steps applied per side and each expectation's result on A and B, marked when the two differ. Expectations never change the verdict. Their `within` is scaled by the time factor like the steps, while Slurm's own node timeouts are not, so an expectation about Slurm noticing a dead node can fail at a high factor.
- **Seed.** A scenario has no randomness, so `--seed` changes nothing in it.
- **Not run live yet** (`./scripts/verify.sh --unit lab_scenario --clean`).

Files of a run, under `compose/generated/lab/NAME/`:

| Path | What |
|---|---|
| `reports/RUN/jobs.json` | the frozen jobs (read-only), lab users only |
| `reports/RUN/scenario.json` | with `--scenario`: the frozen scenario as the run plays it (read-only), lab node names |
| `reports/RUN/run.json`, `progress.json` | the state, history, seed, run length, progress (`percent`, `submitted`, `running`, `pending`, `paused_s`) |
| `reports/RUN/a/sacct.txt`, `b/sacct.txt` | each side's jobs from accounting |
| `reports/RUN/compare.json` | jobs paired by key |
| `reports/RUN/report.md`, `report.json` | the metrics, findings, verdict, suggestions, thresholds and what was not measured (issue 555) |
| `snapshots/RUN/` | the saved state (mode 700); deleted when the run ends, kept only when an abort's restore could not run |

**If a run is refused or ends early**

| Message | Fix |
|---|---|
| `the governor is not reachable` | `./scripts/benchctl start` |
| `refused at L2: lab runs start only at L0-L1 (GV-8)` | wait for the memory level to drop (`benchctl status`) |
| `refused: N MB would leave ...` | free memory, or fewer lab nodes (`lab.max_nodes`) |
| `lab run ... is in progress` | one run at a time: wait, or `benchctl stop lab NAME` |
| `aborted under memory pressure (L3)` | start a new run when memory is back |
| `the replay driver ended without a result` | is the cluster up and the slurm-ctl image built (`benchctl up`)? |
| `the governor did not answer ...: the restore was not run` | `benchctl remove lab NAME` returns to the plain cluster |
| `refused: lab run ... is replay_a. Either ...` (down, stop) | `benchctl stop lab NAME` and wait, or add `--force` |
| `... is being recovered after a governor restart` | wait: `benchctl status` shows when it is done |
| `restore skipped (benchctl down --force)` | `benchctl remove lab NAME` returns to the plain cluster |

## Secrets

These become `<scrubbed>`:
- the values of `StoragePass`, `AccountingStoragePass`, `JwtKey`, `AuthInfo` and `AuthAltParameters` (the key path);
- any key whose name holds Pass, Password, Secret or Token;
- any munge key reference in `slurm.conf`.

A scan then looks for what is left: the token and key patterns of `scripts/check-privacy.sh`, and any `password`, `secret`, `token` or `api_key` assignment with a real value.

If one is found, **nothing is stored**. One message names `file:line [rule]`, never the value. Remove it from the exported file and import again.

Imported files stay local: `compose/generated/` is gitignored and `scripts/check-privacy.sh` refuses a commit that stages it.

## Troubleshooting

| Message | Fix |
|---|---|
| `lab NAME already exists` | pick another name, or add `--replace` |
| `refused: a secret may remain in the export at FILE:LINE [RULE]` | delete that value from the export, import again |
| `FILE lacks the column(s) ...` | re-export with the `-o` list above |
| `no recognised file` | the folder needs at least one file from the table |
| `the governor is not reachable` | `./scripts/benchctl start`; apply and remove never run without it |
| `refused: ... already exists and was not made by lab NAME` | rename the object in the lab files, or remove it from the cluster, then apply again |
| `scontrol show config differs from the rendered settings` | the lab stays active; `benchctl remove lab NAME` returns to the plain cluster |
| `lab X is active: remove it first` | one lab at a time: `benchctl remove lab X` |
| `refused at L2: ...` (validate) | wait for the memory level to drop, or check `./scripts/benchctl status` |
| `the governor is not reachable` (validate) | `./scripts/benchctl start` |
| `could not create the scratch database` (sacctmgr load) | run `./scripts/benchctl up` (it applies the database grant), then validate again |
| `skipped: ... is not running` | start that part (`benchctl up --with accounting`, `--with k8s`, `--with teleport`) and run again |

## Under the hood

Source: `benchctl/lab.py` (store, manifest, version match, plan), `benchctl/lab_import.py` (parsers, scrubber), `benchctl/lab_slurm.py` (key classes, time scaling, downscale), `benchctl/lab_replay.py` (sampling, users, dilation, concurrency), `benchctl/lab_candidate.py` (candidate overlay), `benchctl/lab_diff.py` (diff), `benchctl/lab_apply.py` (apply, remove, marker, slurm.conf overlay; every cluster call goes through an injectable runner, so the tests need no Docker), `benchctl/lab_validate.py` (render, one-shots, results; [ADR 0081](../adr/0081-config-lab-validation.md)).

The run:
- `benchctl/lab_run.py`: run states, frozen jobs file, user remap, seed, A/B pairing.
- `benchctl/lab_snapshot.py` with `images/slurm-ctl/restore-state.sh`: snapshot, restore, hash compare, collecting a side's accounting.
- `benchctl/lab_steps.py`: the host side of each state of a run.
- `images/slurm-ctl/lab_driver.py`: the replay driver.
- `governor/labrun.py`: `lab-run`, `lab-stop`, the supervising thread, admission, the pause and abort flags.

The store is `compose/generated/lab/NAME/baseline/` (scrubbed copies) and `manifest.json` (files with counts and hashes, scrubbed counts, dropped kinds, versions, the workbench's own versions and `version_match`: same, differs or unknown; `differs` marks that component's results indicative). The import writes a temporary folder and renames it, so a refused import leaves nothing. Slurm keys are all stored (secrets scrubbed); `KEY_CLASS` in `lab_slurm.py` decides which are applied, kept or reported "not tested".

## The Lab page (issue 556)

Once a lab exists, **Lab** appears in the page's left navigation (nothing shows without one). It follows the steps above: stepper, imported files, version match, downscale, the candidate diff with an effect tag per line, the validation rows, the run buttons, and the last report with its verdict, wait bars, **Export** (anonymized or full names) and **Copy as PR comment**.
- The buttons ask the governor (apply, back to the plain cluster, run A/B or candidate only, stop). They work only at memory level L0 or L1; a refusal is shown as one message.
- Import stays on the command line: `benchctl import lab NAME --from DIR`.
