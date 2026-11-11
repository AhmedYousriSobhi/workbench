# ADR 0046: Cluster setup shows a review of changes before Apply (redesign step 6d)

Status: accepted. Part of the UI redesign (epic 491); builds on ADR 0043 to 0045. Review item U6.

## Context

Applying a plan can remove nodes and their running jobs and restart slurmctld. The sheet gave a generic two-press confirmation and one line of node counts.
The approved screen (`docs/design/ui-redesign/mockups/Setup.dc.html`) shows adds, removes, restarts and a running-jobs warning before Apply.

## Decision

- `planmodel.js` (pure, tested) compares the cluster the page already holds with the plan inputs and returns adds, removes and restarts. A restart is listed only
  for the two causes `benchctl/stack.py` has: the Slurm node list changed, or accounting was added. Nothing else is claimed.
- Running jobs: a Slurm node to be removed whose state is `allocated` or `mixed` (one `sinfo` through the governor, read when the sheet opens, `/api/slurm-stats`)
  means its jobs stop; Apply is disabled until "I understand" is ticked. If the states cannot be read the sheet says "not read" and does not block, never guessing.
  The page lists nodes, not job ids or users.
- No new route. The configuration cards are compact (steppers share a row, Kubernetes collapses when off).

## Not in this step

Stage progress from the governor (U5; needs a job id and stage events in the governor's status) and job ids per node (a governor `squeue`).
