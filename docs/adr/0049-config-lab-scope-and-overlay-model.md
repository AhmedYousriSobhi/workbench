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
