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

- **Nodes.** `lab.max_nodes` (default 24, at most 64; set it in `sim.yaml`, the commented `lab:` block, or per call with `--max-nodes`). Every (partition x node shape x topology leaf) group keeps at least one node and the rest is split in proportion to the production counts. If there are more groups than the budget the command says so and how many nodes it needs; above 64 groups, drop a partition, shape or switch from the export. Production node names are kept as the Slurm `NodeName` and hostname; the compose services are `lab-node-001`, `lab-node-002`, ... A ratio above 100 production nodes per lab node marks the results indicative.
- **Jobs.** The trace is sampled at the same ratio (a lab node's share of the production nodes), per (account, QOS, partition) stratum, at least one job per stratum, the same jobs every time. Users become `lab-u001`, `lab-u002`, ... unless `--keep-users`. If the sampled trace would run more than `lab.max_running_jobs` (default 50) at once, a smaller node count is proposed instead of throttling submissions, so both runs see the same load.
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

It runs through the governor (`lab-validate`) only when the memory level is L0 or L1, one run at a time; otherwise one message says why (`refused at L2: ...`). The one-shots are class P5, have no `workbench.class` label, are removed afterwards and the scratch database is dropped. Accounting: `benchctl up` gives the database user `slurm` the rights slurmdbd needs (create, drop, alter, index, select, insert, update, delete, lock tables, create temporary tables) on databases named `lab_validate_*` only, never on the real accounting database and without grant option, every time it runs, so an existing stack needs no restart. Each run first drops any `lab_validate_*` database an interrupted run left.

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

**The marker.** `compose/generated/lab/active.json` says which lab is on the cluster (name, side, applied keys, `time_scale`, what it created). The governor `status` carries it as `lab`, and the page shows "Lab NAME applied" in the top strip, so nobody tests the plain cluster by mistake. `benchctl up` and `scale` regenerate `slurm.conf` and put the lab's keys back (one line: `lab NAME is active: its settings were re-applied`); `benchctl down` keeps the marker, so a restarted cluster still has the lab. Only `benchctl remove lab NAME` ends it. If accounting, Kubernetes or Teleport is not running when you remove a lab that made objects there, remove refuses with one message and the lab stays active: start that part and run it again. Rows `sacctmgr` refuses to delete are listed.

## Secrets

The values of `StoragePass`, `AccountingStoragePass`, `JwtKey`, `AuthInfo`, `AuthAltParameters` (the key path), any key whose name holds Pass, Password, Secret or Token, and any munge key reference in `slurm.conf` become `<scrubbed>`. After that a scan looks for what is left: the token and key patterns of `scripts/check-privacy.sh` and any `password`, `secret`, `token` or `api_key` assignment with a real value. If one is found, **nothing is stored** and one message names `file:line [rule]` (never the value). Remove it from the exported file and import again.

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

Source: `benchctl/lab.py` (store, manifest, version match, plan), `benchctl/lab_import.py` (parsers, scrubber), `benchctl/lab_slurm.py` (key classes, time scaling, downscale), `benchctl/lab_replay.py` (sampling, users, dilation, concurrency), `benchctl/lab_candidate.py` (candidate overlay), `benchctl/lab_diff.py` (diff), `benchctl/lab_apply.py` (apply, remove, marker, slurm.conf overlay; every cluster call goes through an injectable runner, so the tests need no Docker), `benchctl/lab_validate.py` (render, one-shots, results; [ADR 0081](../adr/0081-config-lab-validation.md)). The store is `compose/generated/lab/NAME/baseline/` (scrubbed copies) and `manifest.json` (files with counts and hashes, scrubbed counts, dropped kinds, versions, the workbench's own versions and `version_match`: same, differs or unknown; `differs` marks that component's results indicative). The import writes a temporary folder and renames it, so a refused import leaves nothing. Slurm keys are all stored (secrets scrubbed); `KEY_CLASS` in `lab_slurm.py` decides which are applied, kept or reported "not tested".
