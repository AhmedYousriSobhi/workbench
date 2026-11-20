# 10. Keep your machine safe

**You will:** read the memory governor's state, see what it does at each level, and keep a start from hurting your desktop. About 5 minutes.

## You need

- Nothing running for step 1. The rest needs `./scripts/benchctl start` (it starts the governor).

## Steps

1. Check a profile fits before you build it.

   ```bash
   ./scripts/benchctl preflight --profile std
   ```

2. Start with the launcher, so the governor runs.

   ```bash
   ./scripts/benchctl start
   ```

3. Read the level, the signals and the last actions. The first line is the level and free memory (`level L0  [root mode]  avail 6.01 GB ...`); recent governor actions follow, and the last lines say `cluster: N containers running`.

   ```bash
   ./scripts/benchctl status
   ```

4. Ask for a size and read the verdict (the last line starts `fits:` and subtracts what your running cluster already uses). The plan reserves 800 MB for the app under test.

   ```bash
   ./scripts/benchctl plan --cpu-nodes 8 --with jira
   ```

5. Tell the plan to reserve nothing for the app under test (`--app-mb 0`; use it when the app is already running and counted in free memory).

   ```bash
   ./scripts/benchctl plan --cpu-nodes 8 --app-mb 0
   ```

## What the levels mean

- `L0` and `L1`: normal. Starts and `scale` are allowed.
- `L2` and above: the governor refuses `up` and `scale`.
- Higher levels pause, then stop, containers by priority: the mocks first, your app and the governor never.

## You should see

- `status` prints `L0` while there is room.
- Step 5's `needs` figure is 800 MB lower than step 4's, other things equal.
- `plan` rejects sizes over 64 CPU nodes or 16 GPU nodes (`invalid: cpu_nodes must be 0 to 64, not 100`).
- A start that does not fit is refused with the figures, not started.

## If it does not work

- Every start refused: free memory (browser, IDE), run `status`, retry. That is not a code failure.

## Next

- [The governor](../manual/07-governor.md): levels and what is paused.
- [How much memory it uses](../guides/memory.md).
