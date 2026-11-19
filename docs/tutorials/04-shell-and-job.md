# 4. A shell and a job

**You will:** open a shell on a node, run a job, and see it in the queue. About 5 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)).

## Steps

1. Open a shell on a compute node as root.

   ```bash
   ./scripts/benchctl ssh cn001
   ```

2. Open one as the simulated user `alice`.

   ```bash
   ./scripts/benchctl ssh alice@cn001
   ```

3. Run one command instead of a shell.

   ```bash
   ./scripts/benchctl exec gpu001 -- nvidia-smi
   ```

4. Submit a job that holds 4 GPUs for 10 minutes.

   ```bash
   ./scripts/benchctl submit gpu001 --gpus 4 --duration 600
   ```

5. See the job and the node.

   ```bash
   ./scripts/benchctl describe node gpu001
   ```

6. Get a steady background of jobs.

   ```bash
   ./scripts/benchctl load on --rate 6
   ./scripts/benchctl load off
   ```

## You should see

- `squeue` on the login node lists the job; the node shows `allocated`.
- The GPU metrics show those 4 GPUs busy within about 20 seconds.

## If it does not work

- `ssh` asks for a password: the key is in `compose/generated/secrets/ssh/`; see [Nodes and shells](../manual/04-nodes-and-shells.md).

## Next

- [Connect your client](05-connect-your-client.md)
- [Using the cluster](../manual/03-using-the-cluster.md): Slurm, the mocks, Grafana.
