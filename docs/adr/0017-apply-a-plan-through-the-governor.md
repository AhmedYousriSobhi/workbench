# ADR 0017: apply a plan from the page through the governor

Status: accepted (issue 117)

## Context
The Plan tab only printed a command, so editing the numbers appeared to do nothing. The page must not get Docker or
root access (SPEC W-3), but the governor already runs `scale` and `set-profile` through `benchctl` as the checkout owner,
only at L0-L1 (GV-8, ADR 0010).

## Decision
- New governor command `apply-plan {cpu_nodes, gpu_nodes, gpus_per_node, parts}`. The governor validates bounded
  integers and an allow-list of part names (nothing free-form reaches a command line) and runs
  `benchctl up --cpu-nodes ... --with/--without ...` in the background. Refused above L1, in dry-run, or without `--workdir`.
- The page posts `POST /api/plan/apply` (CSRF header, same estimate and fit check as the plan) and shows the governor's
  audit line when the change finishes.
- `benchctl up` for a selection removes node containers the new selection no longer lists and recreates slurmctld so
  `slurm.conf` matches (`stack.up`).

## Consequences
The page can change the cluster only by what the governor allows: counts and parts, not arbitrary commands. A rebuild
takes minutes; the page polls the cluster view and the audit log rather than holding a request open.
