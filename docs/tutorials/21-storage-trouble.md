# 21. Storage trouble

**You will:** fill the shared scratch, put a user over quota, and lose a mount on one node. About 10 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)). Without a `storage:` key in `sim.yaml`, `/home` (NFS) and `/scratch` (Lustre, shared) are declared.

## Steps

1. Read the declared mounts from the login node. `/scratch` is `lustre`, 500T, 40 % used; `/home` is `nfs4`, 20T, 35 % used.

   ```bash
   ./scripts/benchctl ssh alice@login -- df -hT /scratch /home
   ```

2. Fill `/scratch`: 99 % of bytes and 90 % of inodes. Each of the three scenarios takes 300 s (5 minutes) and ends with `scenario passed`; read the effect in a second terminal, for example `./scripts/benchctl ssh alice@login -- 'df -hT /scratch; df -i /scratch; lfs df /scratch'`.

   ```bash
   ./scripts/benchctl scenario run disk-full
   ```

3. Put `alice` over quota. Run the `lfs quota` line in a second terminal while the scenario runs: it shows `21T*` against a `20T` quota and a grace time, then `lfs quota: user alice is over the quota on /scratch`.

   ```bash
   ./scripts/benchctl scenario run quota-exceeded
   ./scripts/benchctl ssh alice@login -- lfs quota -u alice -h /scratch
   ```

4. Make `cn002` lose `/scratch`, as after a stale handle. In a second terminal `./scripts/benchctl exec cn002 -- df /scratch` prints `df: /scratch: Cannot send after transport endpoint shutdown`; `cn001` still answers.

   ```bash
   ./scripts/benchctl scenario run stale-mount
   ```

## You should see

- `df` and `lfs df` show the full mount; `lfs quota` shows a `*` and a grace time.
- On `cn002` `df` fails with the file system's own message (above). Its `node_filesystem` series disappear and `node_filesystem_device_error` is 1 (not exercised here: the Prometheus query).
- Each scenario resets itself after five minutes.

## Remember

- The sizes are declared, not measured. What can really be written is the host's free disk.

## Next

- [Storage](../guides/storage.md): declare your own mounts, and the optional object store.
