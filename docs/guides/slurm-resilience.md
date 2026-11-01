# Slurm resilience: what a running job does when the daemons change under it

Real Slurm is worth its cost for cases a mock cannot reproduce (ADR 0025). These four scenarios start a real job on a
node and then break something under it, and `--trace` prints what Slurm logged and what the job did at every step.

## Run one

```bash
./scripts/benchctl up --profile min                          # first run after an update rebuilds the node image (slurmd-run)
./scripts/benchctl scenario run slurmd-restart-mid-job --trace
```

| Scenario | What it does | What to learn |
|---|---|---|
| `slurmd-restart-mid-job` | kills `slurmd` on `cn001`, the supervisor starts it again, under a running `sleep` job | does `slurmstepd` survive and the job stay RUNNING |
| `slurmd-disabled-mid-job` | keeps `slurmd` down for 45 s, then starts it | the node turns not-responding (`down*`), then returns; what happens to the job |
| `slurmctld-restart-mid-job` | restarts the `slurmctld` container with a real job on `cn001` and a simulated GPU job on `gpu001` | the nodes keep the work; slurmctld recovers state from `StateSaveLocation` |
| `drain-mid-job` | drains `cn001` under a 40 s job | the job finishes; the node shows `draining`, then `drained` |

Two job kinds: a **real** job is a process on `cn001` under a real `slurmstepd` (`sleep`; use a `wrap:` of your own, for
example a capped `stress-ng`). A **simulated** job is one on `gpu001` with `gres: "gpu:2"`: its GPU figures come from
the exporter following the Slurm allocation ([GPU metrics follow Slurm](client-testing.md)). Keep real jobs inside the
governor's memory budget.

The command refuses to start when the governor admits no new work (memory level L2 and above, GV-9) and says so; free memory
and run it again.

## Reading the trace

After each step the trace prints `before:` and `after:` (job states from `squeue`, and per node the `slurmd` and
`slurmstepd` process ids), and between them the new log lines, labelled `slurmctld`, `slurmd` or `slurmstepd`:

```
[  15.0s] ok      slurm {"slurmd_restart": ["cn001"]}
  before: jobs: 7 RUNNING cn001
    cn001: slurmd=7 slurmstepd=112
  log slurmd      cn001   slurmd: Slurmd shutdown completing
  log slurmd      cn001   slurmd: slurmd started on ...
  after:  jobs: 7 RUNNING cn001
    cn001: slurmd=190 slurmstepd=112
```

What to look for:

- **`slurmstepd` pid unchanged** across a `slurmd` restart: the job's step process outlived the daemon. A new pid or `none` means the step died with it.
- **`slurmd` pid changed**: the restart happened. `none` means it is held down (`slurmd-disabled-mid-job`).
- **`slurmctld` lines `Node cn001 not responding`, `set DOWN`** come about 30 s after `slurmd` stops (`SlurmdTimeout=15`).
- **`squeue: no answer from slurmctld`** is expected for a few seconds after `slurmctld_restart`; the jobs return from the saved state.
- **`slurmd` lines `Reattach`/`stepd` after start** show slurmd finding the surviving steps again.

The live test `tests/real/test_slurmresilience_real.py` asserts these on the containers. Where Slurm disagrees with
its documentation the failing message says what was expected and what was seen; the finding is then written below.

## Observed behaviour

Not recorded yet: the live tests have not been run against this change (issue 220). After the first run, add each
disagreement here with the Slurm version and the log lines that show it.

## Limits

- ADR 0002 turns cgroups off (`CgroupPlugin=disabled`, `task/none`, `proctrack/linuxproc`): step cleanup and out-of-memory kills differ from a production site, and a step process that escapes tracking is not killed with the job.
- `slurmd` runs under `slurmd-run` (a small supervisor), not as PID 1, so it can restart without the container. Do not stop it any other way: a bare `docker stop` of a node still stops everything, as a power cut would.
- `slurmctld` is the container's main process: restarting it restarts its container, including the helper that follows allocations for the GPU exporter.

## Scenario steps

`target: slurm` accepts, besides `drain`, `down`, `resume`, `power_off`, `power_on`:

| `do` key | Effect |
|---|---|
| `submit: {node, user, partition, gres, wrap}` | `sbatch` as that user (default `alice`), pinned to `node` |
| `cancel: true` | cancel every job of `alice` |
| `slurmd_restart: [nodes]` | kill `slurmd`; the supervisor starts it again |
| `slurmd_stop: [nodes]` / `slurmd_start: [nodes]` | keep it down / let it start |
| `slurmctld_restart: true` | restart the `slurmctld` container |

`expect` adds `slurm.job_state: {node, state, within}` and `slurm.stepd: {node, alive, within}`. These steps need the host CLI (the page skips them).
