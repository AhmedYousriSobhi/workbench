# 2. Size your cluster

**You will:** choose the nodes and parts you want, see what they cost in memory, and build only if they fit. About 5 minutes.

## You need

- Nothing running. `plan` changes nothing.

## Steps

1. Ask what a size costs.

   ```bash
   ./scripts/benchctl plan --cpu-nodes 2 --gpu-nodes 4 --with jira
   ```

2. Read the last line. `fits` means go on; `does not fit` lists what to drop.

3. Build it.

   ```bash
   ./scripts/benchctl up --cpu-nodes 2 --gpu-nodes 4 --with jira
   ```

4. Change the size later: run `up` again with new numbers (`scale` only works on a preset, see below).

   ```bash
   ./scripts/benchctl up --cpu-nodes 4 --gpu-nodes 4 --with jira
   ```

5. Stop the cluster. The size is remembered: the next `benchctl start` or `up` brings this size back, and `up --profile min` returns to a preset.

   ```bash
   ./scripts/benchctl down
   ```

## Presets instead

- `min`: everyday testing of a client.
- `std`: adds accounting and load.
- `full`: 32 compute nodes, only on a machine with room.

```bash
./scripts/benchctl get profiles
./scripts/benchctl preflight --profile std
```

## You should see

- `plan` prints each item's expected memory and its cap, then the total, and ends with a line that starts `fits:`.
- `up` ends with `stack is up in N s`; the second `up` only adds the nodes that are new (a few seconds).
- `benchctl scale 6` on this selection refuses and tells you to use `up --cpu-nodes N`.
- `up` refuses a size that would not fit; it never starts it and hopes.

## Next

- [Choose the workbench](../guides/choose-workbench.md): every selection flag.
- [How much memory it uses](../guides/memory.md).
