# 11. Fix a problem or report one

**You will:** find out what is wrong, try the usual fixes, and attach one file to a bug report. About 5 minutes.

## Steps

1. Check the machine and Docker. Each line starts `[ok  ]`, `[WARN]` or `[FAIL]`, and a fix follows each WARN and FAIL; the host tuning advice at the end is printed only. `doctor` exits 1 if any line is `[FAIL]`. A `[FAIL] memlock: hard limit 8388608 bytes` means your shell's locked-memory limit is the Ubuntu default (8 MB); the root governor service sets it to unlimited (`./scripts/install-governor.sh`, needs sudo, optional).

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

4. Read one container's log (the last 50 lines; add `--tail N` or `-f`).

   ```bash
   ./scripts/benchctl logs slurmctld
   ```

5. Try the usual fix: stop and start again.

   ```bash
   ./scripts/benchctl stop
   ./scripts/benchctl start
   ```

6. Still broken: write one masked bundle. It prints a file name such as `workbench-diag-20261119-163630.tar.gz`, written in the directory you ran it from (the repository root; git ignores it).

   ```bash
   ./scripts/benchctl diag
   ```

7. Attach the file `diag` prints to the issue. It hides secrets.

## You should see

- `doctor` shows no `[FAIL]` line, or names exactly what to fix.
- Step 5 ends with `stack is up in N s` (about 30 s with Kubernetes and Teleport, 7 s for the smallest profile).
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
