# 12. Compare a production config

**You will:** import a production cluster's exported configuration, check it with the real tools, and compare a changed copy with the original. About 15 minutes.

## You need

- A folder with the exported configuration (see [Config lab](../guides/config-lab.md) for what to export). To try the steps without one, use the sample in the repository: `--from tests/fixtures/lab/full`.
- The governor running: `./scripts/benchctl start`.

## Steps

1. Import it under a name. It lists each file it understood, `scrubbed:` and `dropped, not allow-listed:` lines, the versions, and `not tested:` (what the lab cannot check).

   ```bash
   ./scripts/benchctl import lab prod-a --from ./export
   ```

2. See how it will be scaled down to your machine (for the sample: `6 production nodes -> 6 lab nodes (ratio 1.0x)` and `memory the governor must find free for a run: 328 MB`).

   ```bash
   ./scripts/benchctl get lab prod-a
   ```

3. Check it with the real Slurm and Kubernetes tools. Nothing changes. It starts with `validate lab prod-a (baseline): passed`. The `warning:` lines under `slurm.conf` (for example an unresolvable node name) are expected in the one-shot check.

   ```bash
   ./scripts/benchctl validate lab prod-a
   ```

4. Copy it as a candidate, and edit the copy in `compose/generated/lab/prod-a/candidate/`.

   ```bash
   ./scripts/benchctl add candidate prod-a
   ```

5. See what the edit changes, and whether `slurmctld` restarts. Try it: in `candidate/slurm.conf` change `bf_interval=30` to `bf_interval=60`. It prints `1 change(s)`, the line `~ SchedulerParameters: ... [live]` and `apply: slurmctld reconfigures (no restart)`.

   ```bash
   ./scripts/benchctl diff lab prod-a
   ```

6. Put a side on the running cluster. It refuses when the lab's names clash with the running cluster: a Kubernetes namespace that already exists (`Namespace/team-a already exists and was not made by lab prod-a`) or node names the cluster already has (`lab node name(s) gpu001, gpu002 are also simulated nodes`). The sample does both; rename them in your export, or in the candidate's files, first.

   ```bash
   ./scripts/benchctl apply lab prod-a --side candidate
   ```

7. Put the cluster back to the plain one. If nothing was applied it prints `lab prod-a is not active: nothing to remove`.

   ```bash
   ./scripts/benchctl remove lab prod-a
   ```

## You should see

- Step 3 prints one `PASS` or `FAIL` line per tool (slurm.conf, sacctmgr, promtool, kubectl dry-run, teleport role, secret scan, version check).
- Not exercised here: a successful step 6, because the sample clashes with the default cluster.
- Step 5 lists each changed key and says if a restart is needed.

## Remember

- The replay (`run lab`) is built but not usable end to end. Do not rely on it yet.

## Next

- [Config lab](../guides/config-lab.md): what is read, secrets, the Lab page.
