# 1. Your first cluster

**You will:** start a small cluster, look at it, and stop it. About 10 minutes; the first start builds images and takes longer.

## You need

- Ubuntu with Docker, 16 GB of RAM, no `sudo` for any step below.
- The repository cloned, and `./scripts/check-machine.sh` printing no failure.

## Steps

1. Check the machine. Each line starts `[ok  ]`, `[WARN]` or `[FAIL]`; a fix follows each WARN and FAIL. `[FAIL] memlock` and a swap WARN do not stop a start.

   ```bash
   ./scripts/benchctl doctor
   ```

2. Start everything: governor, cluster and web page. The first run builds images (minutes); later runs take about 7 s for the smallest profile, `min`. Without a saved selection it starts `min`; `start` repeats the last selection (for example one from [tutorial 2](02-size-your-cluster.md)).

   ```bash
   ./scripts/benchctl start
   ```

3. Open the link it prints, or print it again. The link signs you in once and expires after a minute; `open` prints a new one (`http://127.0.0.1:8088/login?code=...`).

   ```bash
   ./scripts/benchctl open
   ```

4. List the nodes (`NAME  KIND  STATE`).

   ```bash
   ./scripts/benchctl get nodes
   ```

5. Read the governor level and the cluster state. The first line is `level L0  [root mode]  avail ... GB`; recent governor actions follow, and the last two lines are `cluster: N containers running` and `web page: http://127.0.0.1:8088/login`.

   ```bash
   ./scripts/benchctl status
   ```

6. Stop everything. It takes about 10 s and prints `stack is down` and `web page stopped`.

   ```bash
   ./scripts/benchctl stop
   ```

## You should see

- Step 2 ends with `stack is up in N s` and a login link for the web page.
- Step 4 lists compute nodes `cn001`...`cn004` and GPU nodes `gpu001`, `gpu002`, all `running`.
- Step 5 shows level `L0` (memory is fine) and `cluster: 22 containers running`.

## If it does not work

- Start refused, low memory: close a browser, run step 2 again.
- Anything else: [Fix a problem or report one](11-fix-or-report.md).

## Next

- [Break a node](03-break-a-node.md)
- [A shell and a job](04-shell-and-job.md)
- The same walk with screenshots: [The Tour](../manual/00-tour.md)
