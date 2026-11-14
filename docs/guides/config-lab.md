# Config lab: import exported production configuration

**In 30 seconds:** copy files you exported from a production cluster into a folder, then `benchctl import lab NAME --from DIR`. The files are parsed, secrets are removed, versions are recorded, and the result is stored under `compose/generated/lab/NAME/` (gitignored). Nothing connects to production.

This is step one of the config lab (goal G6, [ADR 0049](../adr/0049-config-lab-scope-and-overlay-model.md), [ADR 0050](../adr/0050-config-lab-ab-replay-method.md), epic 557). Validation, diff, apply and replay come with later issues; this page says what exists now: import and the downscale plan.

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

## Under the hood

Source: `benchctl/lab.py` (store, manifest, version match, plan), `benchctl/lab_import.py` (parsers, scrubber), `benchctl/lab_slurm.py` (key classes, time scaling, downscale), `benchctl/lab_replay.py` (sampling, users, dilation, concurrency). The store is `compose/generated/lab/NAME/baseline/` (scrubbed copies) and `manifest.json` (files with counts and hashes, scrubbed counts, dropped kinds, versions, the workbench's own versions and `version_match`: same, differs or unknown; `differs` marks that component's results indicative). The import writes a temporary folder and renames it, so a refused import leaves nothing. Slurm keys are all stored (secrets scrubbed); `KEY_CLASS` in `lab_slurm.py` decides which are applied, kept or reported "not tested".
