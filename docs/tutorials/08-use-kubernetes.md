# 8. Use Kubernetes

**You will:** start the Kubernetes part, run `kubectl` against it, and make a node go NotReady. About 10 minutes.

## You need

- Memory for the part: [plan it first](02-size-your-cluster.md).

## Steps

1. Start the cluster with Kubernetes.

   ```bash
   ./scripts/benchctl up --with k8s
   ```

2. Run `kubectl` as `alice` on the login node (port 2222; a second login node, port 2223, exists only if you configured one). The first time, ssh asks you to accept the host key: answer `yes`. Rebuilding the cluster makes a new key; delete the old line from `~/.ssh/known_hosts` if ssh refuses.

   ```bash
   ssh -i compose/generated/secrets/ssh/id_ed25519 -o IdentitiesOnly=yes -p 2222 alice@127.0.0.1 kubectl get nodes
   ```

3. List the pods of every namespace.

   ```bash
   ssh -i compose/generated/secrets/ssh/id_ed25519 -o IdentitiesOnly=yes -p 2222 alice@127.0.0.1 kubectl get pods -A
   ```

4. Make a GPU node NotReady, then recover. It takes 2 minutes: `kgpu001` is down at 0 s and back at 120 s.

   ```bash
   ./scripts/benchctl scenario run k8s-node-notready
   ```

5. Make the API server answer 429 to the user `viewer` (what `alice` is on Kubernetes). It takes 2 minutes. Run `kubectl get nodes` as in step 2 meanwhile, in a burst, to see it.

   ```bash
   ./scripts/benchctl scenario run k8s-api-throttled
   ```

6. See which user sent how many API requests. The command prints one JSON record per request (hundreds of lines); count them by user.

   ```bash
   ./scripts/benchctl get kube-audit | jq -r '.[].user' | sort | uniq -c | sort -rn
   ```

## You should see

- Step 1 ends with `stack is up in N s` and a `kubernetes:` line naming the nodes kcn001, kcn002, kgpu001, kgpu002.
- Step 2 lists those four nodes `Ready`. Step 3 lists pods in `team-a` and `team-b`; some are `CrashLoopBackOff`, `ImagePullBackOff`, `Error` or `Pending` on purpose.
- The nodes go NotReady and back, as the scenario says.
- `get kube-audit` needs the governor service (`benchctl start`). Your `kubectl` calls appear under the user `viewer`; the list also holds the control plane's own requests (leases, controllers).

## Remember

- Pods never really run, so logs are canned lines.
- The login port is 2222 by default; only with several login nodes (for example `--login ssh:slurm,ssh:k8s`) is the Kubernetes login on 2223. The ports are in [Nodes and shells](../manual/04-nodes-and-shells.md).

## Next

- Every Kubernetes read a client makes: [Testing a monitoring client](../guides/client-testing.md#kubernetes-reads-one-by-one).
- Through Teleport: [tutorial 9](09-use-teleport.md).
