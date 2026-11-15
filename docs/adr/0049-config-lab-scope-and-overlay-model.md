# ADR 0049: Config lab: scope, overlay model and governor path

Status: accepted (owner decisions of 2026-11-12). Issue 542; epic 557. Adds goal G6 and requirement C-24; companion to ADR 0050 (A/B method).

## Context

The simulator runs real Slurm, Kubernetes, Prometheus and Teleport. That is enough to test a change to their configuration against a replay of real workload, before it reaches production. The lab must not weaken G5 (test target for a monitoring client), cost new long-running processes, need sudo, or touch production.

## Decision

- **G6, pre-production config lab,** next to G5. G5 stays primary and its tests pass with the lab off.
- **Never connects to production.** Input is files the operator exports; output is a local report. No production credential is asked for or stored.
- **One lab cluster at a time;** A/B runs are sequential with snapshot and restore (ADR 0050). No parallel A/B.
- **The lab is a mode of the running cluster,** not a second stack: `benchctl apply lab NAME` applies the overlay, `benchctl remove lab NAME` returns to the plain `sim.yaml` cluster.
- **Overlay model.** Effective config is generated base (from `sim.yaml`) < lab baseline (imported) < lab candidate (edited). Overlays live under `compose/generated/lab/NAME/` (gitignored); generated files stay generated. `sim.yaml` still defines what runs; an overlay may add policy on top (SPEC section 3).
- **`slurm.conf` key classes** (`benchctl/lab_slurm.py: KEY_CLASS`): policy keys are applied; lab-owned keys (hosts, paths, auth, privileged plugins) keep the base value and are reported "not tested"; unknown keys are applied and flagged. A key ending in `Type`, or in the restart class, or unknown, restarts slurmctld; everything else uses `scontrol reconfigure`. The page shows which before Apply.
- **v1 imports:** `slurm.conf` (policy keys), `topology.conf`, `gres.conf`, `sacctmgr dump`, QOS export, a `sacct --parsable2` trace, Prometheus rules, Kubernetes policy manifests (Namespace, DeviceClass, ResourceClaimTemplate, PriorityClass, ResourceQuota, LimitRange, PodDisruptionBudget, FlowSchema, PriorityLevelConfiguration, Role, ClusterRole, RoleBinding, ClusterRoleBinding), Teleport roles. **Not imported** (listed in every report as "not tested"): `cgroup.conf`, `job_submit.lua`, prolog and epilog scripts, Grafana dashboards, Kubernetes workloads and controllers, Secrets, admission webhooks, unknown CRDs, Alertmanager routing.
- **Secrets are scrubbed on import** (StoragePass, AccountingStoragePass, AuthAltParameters key paths, JwtKey, munge key references, Kubernetes Secrets, Teleport tokens) with the rules of `scripts/check-privacy.sh`; import is refused if an unknown secret remains. `check-privacy.sh` also fails if a file under `compose/generated/lab/` is staged.
- **Versions** (Slurm, Kubernetes, Prometheus, Teleport) are recorded on import; a mismatch shows a banner and marks affected results "indicative".
- **Governor path (owner decision).** Lab commands `lab-validate`, `lab-apply`, `lab-run`, `lab-stop`, `lab-restore` are allow-listed governor commands that run in user mode through the Docker API like `apply-plan` (ADR 0010, 0017, 0048); the root service is not required. Names match `^[a-z0-9][a-z0-9-]{0,40}$` and must exist on disk; L0 and L1 only; audit-logged (GV-6); one message when refused (NF-6). The page never gets Docker (W-3). Governor `status` carries the lab block `{name, state, percent, submitted, running, pending, paused_s}`.
- **Alerts (owner decision).** Prometheus rules with `promtool test rules` come first (issue 460's rules part); Alertmanager and the Jira webhook follow separately. The lab reads firing state from Prometheus `/api/v1/alerts`.

## Consequences

Part A of the epic (real Slurm, Kubernetes and Prometheus features, no new process) is delivered first and benefits G5 on its own. Idle cost is the Part A features plus the lab node containers (about 13 MB each, up to 64; the memory table in the epic counts them). Accounting (about 244 MB) is needed in every profile that uses QOS or fairshare. Previously decided items stand: 453, 455, 457, 478.

## Addendum (issue 550, import)

- The export folder has fixed file names (`slurm.conf`, `topology.conf`, `gres.conf`, `sacctmgr.dump`, `qos.txt`, `trace.sacct`, `k8s/`, `rules/`, `teleport/`, `versions.yaml`); production versions come from `versions.yaml` because none of the other files carries them reliably. A missing version is "not given", never guessed.
- `slurm.conf` is stored whole with secrets scrubbed; the "policy keys only" rule is applied later by `KEY_CLASS` (issue 551), so a key class can change without a re-import.
- The residual secret scan reuses the token rules of `scripts/check-privacy.sh` (a test keeps them in step) and adds assignments to password-like names. It reports `file:line [rule]` only and a refused import writes nothing.

## Addendum (issue 553, diff, apply, remove)

Owner decisions of 2026-11-15.

- **Candidate layout:** `NAME/candidate/` holds the operator's edits with the baseline's file names (`benchctl add candidate NAME` copies the baseline); a file missing there means "as the baseline". `render/` keeps the pre-lab `scontrol show config`, the `sacctmgr` dumps before and after, and the applied keys.
- **Apply scope:** `slurm.conf` keys of class policy, time-scaled and restart only. Unknown keys are **not** applied (this replaces "applied but flagged" for #553; the report lists them as "not applied: unknown"); lab-owned keys keep the base value. Node and partition structure, including settings inside `PartitionName` lines, waits for the run (issue 554). Standalone apply uses `time_scale` 1.0 and says the times are unscaled. An imported QOS or account naming a partition the lab lacks gives a warning.
- **Marker:** `compose/generated/lab/active.json` (name, side, rendered keys, `time_scale`, created roles and objects). The slurm.conf writer puts the keys back on every `up` and `scale`; `down` keeps the marker; only `remove lab` ends a lab. Governor `status` carries the marker as `lab` and the page shows it. After remove, a filtered set of keys (the classes a lab can change plus the marker's own) is compared with the pre-lab snapshot, because `scontrol show config` also holds runtime values.
- **Accounting:** "created by the lab" is decided from the saved dumps (after minus before), never by name; changed rows (`sacctmgr load` also changes existing QOS limits and accounts) are put back from the saved pre-lab dump and QOS table. Without accounting running at remove time, remove refuses with one message and keeps the marker. Rows `sacctmgr` refuses to delete are listed.
- **Prometheus:** rules go to `compose/generated/prometheus/rules/lab/*.yml` (a glob in `prometheus.yml`, so an empty directory is fine), reloaded with SIGHUP through Docker, not with `--web.enable-lifecycle` (which also enables `/-/quit`); `prometheus_config_last_reload_successful` must be 1 afterwards. **Kubernetes** objects carry `workbench.lab=NAME` and are deleted by that label; **Teleport** roles are created with `tctl create -f` and removed by name from the marker. An existing object or role without the lab's label makes apply refuse, before any change.
- **Governor commands of this issue:** `lab-apply` (name, side) and `lab-restore` (name: return to the plain cluster), run as `benchctl apply lab ... --execute` and `benchctl remove lab ... --execute` through the change runner (L0-L1, one at a time, audit line).
- **Names (owner decision 2026-11-15):** `lab-restore` keeps the meaning "return to the plain cluster" (`remove lab`). The A/B snapshot restore of issue 554 gets its own governor verb (for example `lab-snapshot-restore`); `lab-restore` is never used for snapshots.
- **`AccountingStorageType` is lab-owned** (`lab_slurm.KEY_CLASS`): the lab keeps its own accounting setup, apply never changes it, and the diff shows it as "not tested: lab-owned". Applying it could point slurmctld at a database that the plain cluster does not run.
- **Rules check:** before a rule file is written, `promtool check rules` runs through the executor and image of the lab validation (issue 552, ADR 0081); a failure refuses the apply before any change.
