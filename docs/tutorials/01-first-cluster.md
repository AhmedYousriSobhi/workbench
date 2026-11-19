# 1. Your first cluster

**You will:** start a small cluster, look at it, and stop it. About 10 minutes; the first start builds images and takes longer.

## You need

- Ubuntu with Docker, 16 GB of RAM, no `sudo` for any step below.
- The repository cloned, and `./scripts/check-machine.sh` printing no failure.

## Steps

1. Check the machine.

   ```bash
   ./scripts/benchctl doctor
   ```

2. Start everything: governor, cluster and web page.

   ```bash
   ./scripts/benchctl start
   ```

3. Open the link it prints, or print it again.

   ```bash
   ./scripts/benchctl open
   ```

4. List the nodes.

   ```bash
   ./scripts/benchctl get nodes
   ```

5. Read the governor level and the cluster state.

   ```bash
   ./scripts/benchctl status
   ```

6. Stop everything.

   ```bash
   ./scripts/benchctl stop
   ```

## You should see

- Step 2 ends with a login link for the web page.
- Step 4 lists compute and GPU nodes, all running.
- Step 5 shows level `L0` (memory is fine) and the cluster up.

## If it does not work

- Start refused, low memory: close a browser, run step 2 again.
- Anything else: [Fix a problem or report one](11-fix-or-report.md).

## Next

- [Break a node](03-break-a-node.md)
- [A shell and a job](04-shell-and-job.md)
- The same walk with screenshots: [The Tour](../manual/00-tour.md)
