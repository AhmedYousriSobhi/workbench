# 11. Fix a problem or report one

**You will:** find out what is wrong, try the usual fixes, and attach one file to a bug report. About 5 minutes.

## Steps

1. Check the machine and Docker.

   ```bash
   ./scripts/benchctl doctor
   ```

2. Read the governor, the cluster and the page at a glance.

   ```bash
   ./scripts/benchctl status
   ```

3. List the containers and their state.

   ```bash
   ./scripts/benchctl get services
   ```

4. Read one container's log.

   ```bash
   ./scripts/benchctl logs slurmctld
   ```

5. Try the usual fix: stop and start again.

   ```bash
   ./scripts/benchctl stop
   ./scripts/benchctl start
   ```

6. Still broken: write one masked bundle.

   ```bash
   ./scripts/benchctl diag
   ```

7. Attach the file `diag` prints to the issue. It hides secrets.

## You should see

- `doctor` ends with no failure line, or names exactly what to fix.
- `diag` prints the path of one `.tar.gz`.

## Usual causes

| You see | Cause | Do |
|---|---|---|
| a start is refused | low host memory | close apps, `status`, retry |
| the page says the governor is down | not started | `./scripts/benchctl start` |
| `unknown certificate authority` | an old Teleport state | [reset it](../manual/04-nodes-and-shells.md#keep-or-reset-the-teleport-cluster) |

## Next

- Symptoms, causes and fixes: [Troubleshooting](../manual/09-troubleshooting.md).
- Report on GitHub with the `diag` file and the command you ran.
