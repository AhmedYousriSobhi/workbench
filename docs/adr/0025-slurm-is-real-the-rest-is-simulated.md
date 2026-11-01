# ADR 0025: Slurm runs as real daemons; what is real, what is simulated, and what it costs

Status: accepted (records a decision taken in ADR 0002 and ADR 0003; issue 218)

## Context
workbench is the test target for a desktop HPC monitoring client (SPEC G5). That client runs `squeue`, `sinfo` and
`sacct` over SSH and parses their text, and reads `slurmrestd`. The question is whether the scheduler should be a mock
(a process that prints plausible output) or the real software. Memory is the usual argument for a mock; the
`min` profile measured about 0.17 GB loaded with four `slurmd`, so it is not a strong one here.

## Decision
`slurmctld`, `slurmd`, `slurmrestd` and (in accounting profiles) `slurmdbd` run as the real SchedMD binaries
(26.05.5, built from source, ADR 0002). Everything the scheduler sits on is simulated.

| Layer | Real or simulated | Why |
|---|---|---|
| Scheduler logic, state machine, node states (`down*`, `mixed`, `drain`), pending reasons | **Real** | The client's parser and state handling are the thing under test; a mock drifts from production in exactly these places. |
| `squeue`/`sinfo`/`sacct`/`scontrol` text output and flags | **Real** | Column layouts and `--format` strings are produced by Slurm, not re-implemented. |
| `slurmrestd` auth (JWT: 401 without a token, 511 with a bad one, `root` refused) | **Real** | The client must handle the real contract (ADR 0003). |
| Node death detection | **Real** | A frozen container stops answering and `slurmctld` marks it `down*` itself after about 30 s, with real timing. |
| Health check, prolog/epilog requeue | Real Slurm hooks, simulated check (C-15) | The hook path is Slurm's; the checks read simulated metrics. |
| Accounting storage | Real `slurmdbd`, MariaDB on a capped tmpfs | Optional part; without it `sacct` says accounting storage is disabled, as on a real site. |
| GPUs | Simulated: count-based GRES backed by `/dev/null` symlinks | No real device binding is exercised. |
| cgroup enforcement | **Not exercised** (`CgroupPlugin=disabled`, ADR 0002) | Docker mounts the tree read-only; extra privileges were rejected. |
| `slurmrestd` sandboxing | Two `unshare` steps skipped (ADR 0003, 0004) | The container boundary replaces them. |
| Jobs | Synthetic (`sleep`), never real training | Non-goal in SPEC section 1. |
| BCM, Jira, BMC, GPU metrics | Mocks | Only the API surface matters there (ADR 0005, 0012, 0013). |

Rule for new components: simulate the API when the client only consumes responses; run the real software when the
client depends on behaviour that emerges from state and timing, and the real software is small enough for the budget.

## Consequences
- Slurm output and state transitions are correct by construction; tests assert on real containers, not on a fixture.
- Known fidelity gaps stay documented, not hidden: `gres/gpu` is missing from `AllocTRES` without `slurmdbd`; cgroup
  limits, real GPU binding and resource contention are not tested ([client-testing.md](../guides/client-testing.md)).
- Cost: every SchedMD security release means a new pin (version and SHA256 together, ADR 0002); the checksum guards
  against corruption, not a compromised mirror. A rebuild takes a few minutes.
- If memory or build time forces a cut, order of removal: `slurmdbd`/MariaDB (already optional), then `slurmd` count.
  Replacing the scheduler with a mock is not on that list.
