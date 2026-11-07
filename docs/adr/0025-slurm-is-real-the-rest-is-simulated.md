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

## Addendum: resilience tests (issue 220)
The expectation that `slurmstepd` survives a `slurmd` restart and the job reattaches is tested, not assumed:
`scenarios/slurmd-restart-mid-job.yaml` and three siblings, with a log trace ([guide](../guides/slurm-resilience.md)) and
`tests/real/test_slurm_resilience_real.py`. Making this testable changed one thing: `slurmd` no longer runs as the node
container's PID 1 (it would take the container and every step with it) but under `images/node-base/slurmd-run`, a
loop that restarts it unless a hold file exists. `slurmctld` stays PID 1, so its restart is a container restart. The
observed behaviour, including any disagreement with Slurm's documentation, is recorded in the guide after the first live run.

## Addendum: proposed real-entity alternatives (issue 238)
The table above says what is simulated. This one records, for each simulated layer, the real entity that could replace or
back it, so the question is not re-argued. Nothing here is decided except where the status says so; each row has its own
issue. The rule for new components still applies: run the real software when the client depends on behaviour that emerges
from state and timing and it fits the memory budget.

| Simulated layer | Real candidate | Cost and caveat | Status |
|---|---|---|---|
| Host metrics (CPU, memory, disk, NIC) | `node_exporter` in every node | Small; DCGM and InfiniBand series stay synthetic | proposed, #227; inside a container it reports host-wide memory and CPU, so scope it to network, disk and load |
| Node health check runner | Upstream LBNL NHC | Inputs stay simulated; the runner and check semantics become real | to evaluate, #228; BSD-3 licensed, needs Bash 4.3; its GPU and IB checks need hardware, so ours stay custom scripts |
| Jira (mock) | Real Jira Cloud/DC through the existing `cloud`/`dc` modes | Too heavy to run by default; use it to record responses for contract tests (SPEC NF-2) | proposed, #229; compare recorded response shapes, not replayed cassettes |
| BMC (bmc-farm) | DMTF Redfish-Service-Validator against bmc-farm | Does not replace the mock; adds conformance evidence. The DMTF emulator and sushy-tools lack per-node addresses and fault injection | proposed, #230 |
| GPU metrics (hand-written exporter) | Real DCGM with fake GPU entities | Unverified that it runs without hardware and serves the labels the client reads | research, #231; NVIDIA's mock NVML (Apache-2.0, up to 8 GPUs) lets the real dcgm-exporter run without a GPU, memory per node to measure |
| Kubernetes (not built) | k3d/kind plus KWOK (SPEC C-2) | Real from the start; memory budget to declare | blocked on a decision, #232; kind and k3d nodes are privileged by default, which conflicts with the unprivileged-container rule |
| Jobs (`sleep`) | Bounded `stress-ng` | Real CPU and memory load; opt-in | proposed, #233 |

Cannot be real, with the reason:
- **BCM**: proprietary and licensed; only the `cmsh` and CMDaemon surface can be offered (ADR 0005, 0013).
- **NFS/Lustre**: excluded by SPEC C-11.
- **GPUs and GPU binding**: real GPUs are a non-goal (SPEC section 1).
- **cgroup enforcement, link-down and InfiniBand port injection**: need privileges that ADR 0002 rejected.
