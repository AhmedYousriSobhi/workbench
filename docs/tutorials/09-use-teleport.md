# 9. Use Teleport

**You will:** start Teleport, log in as `alice`, and reach Kubernetes through `tsh kube`. About 10 minutes.

## You need

- `tsh` installed. Its major version must not be newer than the Teleport in the cluster.
- `kubectl` on the host, for step 5.
- Memory for Teleport and Kubernetes: [plan it first](02-size-your-cluster.md).
- Teleport's licence: the Teleport image is Teleport's own Community Edition, free only for organisations under 100 employees and under $10 million a year in revenue. This repository only names the image tag; you accept the licence when you start it. Details: [ADR 0020](../adr/0020-teleport-agentless-openssh.md).

## Steps

1. Start both parts.

   ```bash
   ./scripts/benchctl up --with teleport --with k8s
   ```

2. Make a one-time link for `alice`. It prints one `https://10.141.255.30:3080/web/reset/...` link.

   ```bash
   ./scripts/benchctl add invite alice
   ```

3. Open the link in a browser once. Choose a password and scan the code.

4. Log in. The proxy certificate is self-signed, hence `--insecure`.

   ```bash
   tsh --proxy=10.141.255.30:3080 --insecure login --user=alice
   ```

5. Or skip the password: use the identity file and a scratch home.

   ```bash
   export TELEPORT_HOME=$(mktemp -d)
   export KUBECONFIG=$TELEPORT_HOME/kubeconfig
   export TELEPORT_IDENTITY_FILE=compose/generated/secrets/teleport/identity/alice
   export TELEPORT_PROXY=10.141.255.30:3080
   tsh --insecure kube ls
   tsh --insecure kube login workbench-k8s
   kubectl get nodes
   ```

6. Break it on purpose. The first takes 60 s, the second 180 s; run them one after the other and each ends with `scenario passed`. While the first runs, `tsh` hangs without an answer (stop it with Ctrl-C), and the Slurm login on port 2222 still works.

   ```bash
   ./scripts/benchctl scenario run teleport-outage
   ./scripts/benchctl scenario run kube-identity-expired
   ```

## You should see

- `kube ls` lists `workbench-k8s`; `kubectl get nodes` lists the fake nodes.
- During the outage `tsh` and the web terminal fail; the cluster itself stays up.
- Between 60 s and 180 s of the second scenario `tsh kube login` fails with `ssh: cert has expired` and `cannot relogin in non-interactive session`; after `scenario passed` it works again.

## If it does not work

- `unknown certificate authority`, or you want a fresh Teleport: [Keep or reset the Teleport cluster](../manual/04-nodes-and-shells.md#keep-or-reset-the-teleport-cluster).

## Next

- What each user tier may do, and the audit trail: [Kubernetes through Teleport](../guides/teleport-kube.md).
