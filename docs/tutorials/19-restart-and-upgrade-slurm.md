# 19. Restart and upgrade Slurm under running jobs

**You will:** restart a daemon while a job runs, then upgrade Slurm in place, and check the job survives. About 15 minutes.

## You need

- The governor running, so the trace works: `./scripts/benchctl start`.

## Steps

1. Restart `slurmd` under a real job and print what Slurm did at each step.

   ```bash
   ./scripts/benchctl scenario run slurmd-restart-mid-job --trace
   ```

2. Restart `slurmctld` under a job.

   ```bash
   ./scripts/benchctl scenario run slurmctld-restart-mid-job --trace
   ```

3. Break one step by hand.

   ```bash
   ./scripts/benchctl submit cn001 --duration 300
   ./scripts/benchctl set slurmd cn001 restart
   ./scripts/benchctl get trace --node cn001 --since 60
   ```

4. Choose a Slurm release for a new cluster.

   ```bash
   ./scripts/benchctl up --cpu-nodes 2 --slurm-version 25.11.9
   ./scripts/benchctl exec slurmctld -- slurmctld -V
   ```

5. Upgrade in place under a job. Start on an older release first (the scenario names it).

   ```bash
   ./scripts/benchctl scenario run slurm-upgrade-mid-job --trace
   ```

6. Clear the leftover jobs.

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
