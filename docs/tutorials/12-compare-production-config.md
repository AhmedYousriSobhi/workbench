# 12. Compare a production config

**You will:** import a production cluster's exported configuration, check it with the real tools, and compare a changed copy with the original. About 15 minutes.

## You need

- A folder with the exported configuration (see [Config lab](../guides/config-lab.md) for what to export).
- The governor running: `./scripts/benchctl start`.

## Steps

1. Import it under a name.

   ```bash
   ./scripts/benchctl import lab prod-a --from ./export
   ```

2. See how it will be scaled down to your machine.

   ```bash
   ./scripts/benchctl get lab prod-a
   ```

3. Check it with the real Slurm and Kubernetes tools. Nothing changes.

   ```bash
   ./scripts/benchctl validate lab prod-a
   ```

4. Copy it as a candidate, and edit the copy in `compose/generated/lab/prod-a/candidate/`.

   ```bash
   ./scripts/benchctl add candidate prod-a
   ```

5. See what the edit changes, and whether `slurmctld` restarts.

   ```bash
   ./scripts/benchctl diff lab prod-a
   ```

6. Put a side on the running cluster.

   ```bash
   ./scripts/benchctl apply lab prod-a --side candidate
   ```

7. Put the cluster back to the plain one.

   ```bash
   ./scripts/benchctl remove lab prod-a
   ```

## You should see

- Step 3 prints one verdict per tool.
- Step 5 lists each changed key and says if a restart is needed.

## Remember

- The replay (`run lab`) is built but not usable end to end. Do not rely on it yet.

## Next

- [Config lab](../guides/config-lab.md): what is read, secrets, the Lab page.
