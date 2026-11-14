# ADR 0080: Multiple-slurmd and a merged mock process are rejected

Status: accepted by the owner (2026-11-14); issue 457.

## Context
To cut per-container overhead at 32+ nodes, two ideas were raised: build Slurm with `--enable-multiple-slurmd` so one container hosts many logical nodes, and merge the four mocks into one "mock-hub" process.

## Decision
1. **Mock-hub: won't do.** Each mock has its own fixed service address (`SERVICE_HOSTS`); one process in one container has one address, so merging breaks the addressing plan (ADR 0018). It saves about 30 MB of 16 GB.
2. **Multiple-slurmd: never the default and not built now.** Logical nodes share one IP and differ by port, which breaks per-node addresses, `ssh cn001`, per-node fake GPU device files and NHC state, and "freeze one node" (freezing the container takes all its nodes down). SchedMD itself describes the mode as for testing protocol changes and says not all functions work (<https://slurm.schedmd.com/programmer_guide.html>). The rejection rests on fidelity, not numbers.
3. **Revisit** only if a client test needs more than about 64 Slurm nodes, or `benchctl measure` shows per-container overhead breaking a budget. It would then be an explicit opt-in scale mode in its own issue.

## Consequences
The only Slurm scaling lever left is the existing one: shared base image, tiny limits, the static BMC forwarder (issue 454). The architecture sections that called multiple-slurmd the 32+ node mitigation are updated.
