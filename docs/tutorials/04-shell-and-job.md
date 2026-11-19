# 4. A shell and a job

**You will:** open a shell on a node, run a job, and see it in the queue. About 5 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)).

## Steps

1. Open a shell on a compute node as root (inside the container, not on your host). Type `exit` to leave.

   ```bash
   ./scripts/benchctl ssh cn001
   ```

2. Open one as the simulated user `alice`.

   ```bash
   ./scripts/benchctl ssh alice@cn001
   ```

3. Run one command instead of a shell. The GPUs are simulated: there is no `nvidia-smi` on the nodes, only 8 fake device files.

   ```bash
   ./scripts/benchctl exec gpu001 -- ls /dev/workbench-gpu0 /dev/workbench-gpu7
   ```

4. Submit a job that holds 4 GPUs for 10 minutes.

   ```bash
   ./scripts/benchctl submit gpu001 --gpus 4 --duration 600
   ```

5. See the job and the node. Run it again after about 20 seconds: `GPUs:` changes from `busy none` to `busy [0, 1, 2, 3]`.

   ```bash
   ./scripts/benchctl describe node gpu001
   ```

6. Get a steady background of jobs (`load on` prints `load: on, about 6 job(s) a minute`; the first CPU job shows in `squeue` within seconds). Cancel your job when done: `benchctl exec login -u alice -- scancel 1`.

   ```bash
   ./scripts/benchctl load on --rate 6
   ./scripts/benchctl load off
   ```

## You should see

- Step 4 prints `gpu001: job 1 submitted for 600 s`.
- `squeue` on the login node lists the job (`benchctl exec login -u alice -- squeue`); the node shows `MIXED` (4 of 8 GPUs held), not `allocated`.
- The GPU metrics show those 4 GPUs busy within about 20 seconds.

## If it does not work

- `ssh` asks for a password: the key is in `compose/generated/secrets/ssh/`; see [Nodes and shells](../manual/04-nodes-and-shells.md).

## Next

- [Connect your client](05-connect-your-client.md)
- [Using the cluster](../manual/03-using-the-cluster.md): Slurm, the mocks, Grafana.
