# ADR 0016: choose the workbench by selection; the built truth is a generated sim copy

Status: accepted (issue 92)

## Context
Node counts and parts lived in `sim.yaml` plus a size preset. A test needs different mixes (2 CPU and 4 GPU nodes with
Jira), and the mocks, the GPU exporter and `slurm.conf` must agree on the node list. The mocks mounted the repository's
`sim.yaml`, so a preset such as `std` (16 compute nodes) disagreed with what they listed.

## Decision
- A `Spec` (flags or a workbench file) is turned into an effective `sim` dict and a profile dict (`groups` plus `drop`
  for unselected services); `simctl/workbench.py`. The estimate is the sum of the generated container limits plus the
  always-on observability stack, an upper bound, the figure the governor already admits against.
- `compose.write` writes the effective sim to `compose/generated/sim.yaml`; the BMC farm, BCM mock and GPU exporter mount
  that copy. `slurm.conf` and the copy come from the same dict, so they cannot disagree.
- `benchctl plan` changes nothing and exits 1 when the estimate does not fit; `up` with a selection (or `apply -f`)
  admits by the estimate and saves the spec to `compose/generated/workbench.yaml`. A plain `up --profile` forgets it.
- Every command reads the effective sim while a workbench is saved (`get`, `describe`, `export`, scenarios, the page).
- Kubernetes nodes are refused until they are built. The web page estimates only (`/api/plan`); it never starts
  containers (SPEC W-3).

## Consequences
The estimate is conservative: real use is lower, and no 15 percent accuracy claim is made. `scale` is refused while a
workbench is built, to avoid two ways of changing one number.
