# ADR 0033: The fidelity VM is deferred

Status: accepted (issue 366, closes issue 4)

## Context
SPEC C-10 asked for one on-demand libvirt Ubuntu VM (2 GB cap, power through sushy-tools) for what containers cannot prove.
workbench is the test target for a desktop HPC monitoring client (SPEC G5). Its endpoints are an SSH login node, Slurm,
Grafana GPU metrics, Jira search and Redfish BMC. Containers and bmc-farm already serve all of them. A 2 GB VM is a large
cost on a 16 GB host and nothing in G5 needs it.

## Decision
- C-10 is deferred: not built, not required, not part of any profile.
- The scaffold in `fidelity-vm/` (Vagrantfile, `up.sh` with 4 retries, 2 GB cap) stays in the repository, unverified and unsupported.
- `benchctl doctor` keeps its libvirt and KVM checks as warnings only.
- Revisit when a client feature needs a real Linux host (for example its agent install path).

## Consequences
- No requirement in SPEC depends on the VM. Phase 6 of the roadmap is deferred.
- The `full` profile estimate no longer counts the VM.
- Issue 4 is closed as not planned.
