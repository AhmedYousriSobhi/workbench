# 8. Use Kubernetes

**You will:** start the Kubernetes part, run `kubectl` against it, and make a node go NotReady. About 10 minutes.

## You need

- Memory for the part: [plan it first](02-size-your-cluster.md).

## Steps

1. Start the cluster with Kubernetes.

   ```bash
   ./scripts/benchctl up --with k8s
   ```

2. Run `kubectl` as `alice` on the Kubernetes login node.

   ```bash
   ssh -i compose/generated/secrets/ssh/id_ed25519 -o IdentitiesOnly=yes -p 2223 alice@127.0.0.1 kubectl get nodes
   ```

3. List the pods of every namespace.

   ```bash
   ssh -i compose/generated/secrets/ssh/id_ed25519 -o IdentitiesOnly=yes -p 2223 alice@127.0.0.1 kubectl get pods -A
   ```

4. Make a GPU node NotReady, then recover.

   ```bash
   ./scripts/benchctl scenario run k8s-node-notready
   ```

5. Make the API server slow.

   ```bash
   ./scripts/benchctl scenario run k8s-api-throttled
   ```

6. See which user sent how many API requests.

   ```bash
   ./scripts/benchctl get kube-audit
   ```

## You should see

- Fake nodes and pods; some pods crash-loop on purpose.
- The nodes go NotReady and back, as the scenario says.
- `get kube-audit` needs the governor service (`benchctl start`); it counts nodes and pods only.

## Remember

- Pods never really run, so logs are canned lines.
- The ports (2222 Slurm login, 2223 Kubernetes login) are in [Nodes and shells](../manual/04-nodes-and-shells.md).

## Next

- Every Kubernetes read a client makes: [Testing a monitoring client](../guides/client-testing.md#kubernetes-reads-one-by-one).
- Through Teleport: [tutorial 9](09-use-teleport.md).
