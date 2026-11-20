# 3. Break a node

**You will:** take a node down on purpose and watch Slurm notice and recover. About 5 minutes.

## You need

- A running cluster: `./scripts/benchctl start` ([tutorial 1](01-first-cluster.md)).

## Steps

1. See the scenarios.

   ```bash
   ./scripts/benchctl scenario list
   ```

2. Read what `node-down` would do, without doing it.

   ```bash
   ./scripts/benchctl scenario run node-down --dry-run
   ```

3. Run it. It takes about 40 s and picks `cn002`; it ends with `scenario passed`. Do steps 4 and 5 in a second terminal while it runs.

   ```bash
   ./scripts/benchctl scenario run node-down
   ```

4. Watch the node state change. While `cn002` is off, `get nodes` shows it `paused` and `describe` shows `IDLE+DRAIN+NOT_RESPONDING` with reason `node down`.

   ```bash
   ./scripts/benchctl get nodes
   ./scripts/benchctl describe node cn002
   ```

5. Wait for it to be back, from a script if you like.

   ```bash
   ./scripts/benchctl wait node-state cn002 running --timeout 120
   ```

## Break one thing by hand

```bash
./scripts/benchctl set node cn001 drain --reason "disk check"
./scripts/benchctl set node cn001 resume
./scripts/benchctl get faults
./scripts/benchctl clear faults
```

## You should see

- Step 2 lists six timed steps (BMC off and BCM `DOWN` at 0 s, Slurm drain at 5 s, all back at 40 s).
- Step 5 prints a JSON block with `"outcome": "met"`.
- The BMC and BCM mocks and Slurm all show the node down, then up again.
- The page's node panel (`./scripts/benchctl open`) shows the same, with the scenario in its timeline.

## Next

- Faster: add `--speed 10` to `scenario run`.
- Every scenario: [Scenarios and faults](../manual/06-scenarios.md).
- Write your own: the same chapter, last section.
