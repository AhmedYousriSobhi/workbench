# Config lab: import exported production configuration

**In 30 seconds:** copy files you exported from a production cluster into a folder, then `benchctl import lab NAME --from DIR`. The files are parsed, secrets are removed, versions are recorded, and the result is stored under `compose/generated/lab/NAME/` (gitignored). Nothing connects to production.

This is step one of the config lab (goal G6, [ADR 0049](../adr/0049-config-lab-scope-and-overlay-model.md), [ADR 0050](../adr/0050-config-lab-ab-replay-method.md), epic 557). Validate, diff, apply and replay come with later issues; this page says what exists now.

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

Source: `benchctl/lab.py` (store, manifest, version match), `benchctl/lab_import.py` (parsers, scrubber). The store is `compose/generated/lab/NAME/baseline/` (scrubbed copies) and `manifest.json` (files with counts and hashes, scrubbed counts, dropped kinds, versions, the workbench's own versions and `version_match`: same, differs or unknown; `differs` marks that component's results indicative). The import writes a temporary folder and renames it, so a refused import leaves nothing. Slurm keys are all stored (secrets scrubbed); which are applied, kept or reported "not tested" is decided by the key classes of a later issue (551).
