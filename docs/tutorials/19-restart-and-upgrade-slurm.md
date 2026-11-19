# 19. Restart and upgrade Slurm under running jobs

**You will:** restart a daemon while a job runs, then upgrade Slurm in place, and check the job survives. About 15 minutes.

## You need

- The governor running, so the trace works: `./scripts/benchctl start`.

## Steps

1. Restart `slurmd` under a real job and print what Slurm did at each step. It takes 60 s and prints `before:` and `after:` blocks with the job state and the `slurmd` and `slurmstepd` process ids (here `slurmstepd=1388` stays while `slurmd` goes `none`, then gets a new id).

   ```bash
   ./scripts/benchctl scenario run slurmd-restart-mid-job --trace
   ```

2. Restart `slurmctld` under a job (75 s; a CPU job on `cn001` and a GPU job on `gpu001` both stay `RUNNING`).

   ```bash
   ./scripts/benchctl scenario run slurmctld-restart-mid-job --trace
   ```

3. Break one step by hand. The first line prints `cn001: job N submitted for 300 s`; the trace is one JSON document with the job states, the `slurmd` and `slurmstepd` ids and the new log lines. Run it right away: the job may still be `PENDING` for a second or two, so `slurmstepd` can be `none` in the first answer.

   ```bash
   ./scripts/benchctl submit cn001 --duration 300
   ./scripts/benchctl set slurmd cn001 restart
   ./scripts/benchctl get trace --node cn001 --since 60
   ```

4. Choose a Slurm release for a new cluster. The first time a release is used it is compiled (minutes); later runs reuse the image (here about 11 s). `slurmctld -V` prints `slurm 25.11.9`.

   ```bash
   ./scripts/benchctl up --cpu-nodes 2 --slurm-version 25.11.9
   ./scripts/benchctl exec slurmctld -- slurmctld -V
   ```

5. Upgrade in place under a job. The scenario needs the accounting part, four CPU nodes, two GPU nodes and the older release, or it stops with `invalid: ... sim.yaml has 2 cpu node(s)`. Start that first. The run takes 120 s and ends with `scenario passed`. Afterwards the cluster runs 26.05.5 (`slurmctld -V`); to repeat, run the `up` line again.

   ```bash
   ./scripts/benchctl up --cpu-nodes 4 --gpu-nodes 2 --with accounting --slurm-version 25.11.9
   ./scripts/benchctl scenario run slurm-upgrade-mid-job --trace
   ```

6. Clear the leftover jobs (prints `jobs cancelled`).

   ```bash
   ./scripts/benchctl remove jobs
   ```

## You should see

- After a `slurmd` restart the `slurmstepd` process survives and the job stays `RUNNING`.
- The upgrade runs in SchedMD's order: `slurmdbd`, `slurmctld`, `slurmd`, then the login node. The job must survive it.
- `--trace` prints `before:` and `after:` with job states and process ids, and the new log lines.

## Next

- [Slurm resilience](../guides/slurm-resilience.md)
- [Choose the Slurm version](../guides/slurm-version.md): add another release.
