# 21. Storage trouble

**You will:** fill the shared scratch, put a user over quota, and lose a mount on one node. About 10 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)). Without a `storage:` key in `sim.yaml`, `/home` (NFS) and `/scratch` (Lustre, shared) are declared.

## Steps

1. Read the declared mounts from the login node.

   ```bash
   ./scripts/benchctl ssh alice@login -- df -hT /scratch /home
   ```

2. Fill `/scratch`: 99 % of bytes and 90 % of inodes.

   ```bash
   ./scripts/benchctl scenario run disk-full
   ```

3. Put `alice` over quota.

   ```bash
   ./scripts/benchctl scenario run quota-exceeded
   ./scripts/benchctl ssh alice@login -- lfs quota -u alice -h /scratch
   ```

4. Make `cn002` lose `/scratch`, as after a stale handle.

   ```bash
   ./scripts/benchctl scenario run stale-mount
   ```

## You should see

- `df` and `lfs df` show the full mount; `lfs quota` shows a `*` and a grace time.
- On `cn002` `df` fails with the file system's own message. Its `node_filesystem` series disappear and `node_filesystem_device_error` is 1.
- Each scenario resets itself after five minutes.

## Remember

- The sizes are declared, not measured. What can really be written is the host's free disk.

## Next

- [Storage](../guides/storage.md): declare your own mounts, and the optional object store.
