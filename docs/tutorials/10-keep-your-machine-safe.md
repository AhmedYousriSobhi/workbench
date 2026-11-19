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

3. Read the level, the signals and the last actions.

   ```bash
   ./scripts/benchctl status
   ```

4. Ask for a size and read the verdict.

   ```bash
   ./scripts/benchctl plan --cpu-nodes 8 --with jira
   ```

5. Tell the plan the app under test already runs, so its memory is not counted twice.

   ```bash
   ./scripts/benchctl plan --cpu-nodes 8 --app-mb 0
   ```

## What the levels mean

- `L0` and `L1`: normal. Starts and `scale` are allowed.
- `L2` and above: the governor refuses `up` and `scale`.
- Higher levels pause, then stop, containers by priority: the mocks first, your app and the governor never.

## You should see

- `status` prints `L0` while there is room.
- A start that does not fit is refused with the figures, not started.

## If it does not work

- Every start refused: free memory (browser, IDE), run `status`, retry. That is not a code failure.

## Next

- [The governor](../manual/07-governor.md): levels and what is paused.
- [How much memory it uses](../guides/memory.md).
