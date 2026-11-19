# 9. Use Teleport

**You will:** start Teleport, log in as `alice`, and reach Kubernetes through `tsh kube`. About 10 minutes.

## You need

- `tsh` installed. Its major version must not be newer than the Teleport in the cluster.
- Memory for Teleport and Kubernetes: [plan it first](02-size-your-cluster.md).

## Steps

1. Start both parts.

   ```bash
   ./scripts/benchctl up --with teleport --with k8s
   ```

2. Make a one-time link for `alice`.

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

6. Break it on purpose.

   ```bash
   ./scripts/benchctl scenario run teleport-outage
   ./scripts/benchctl scenario run kube-identity-expired
   ```

## You should see

- `kube ls` lists `workbench-k8s`; `kubectl get nodes` lists the fake nodes.
- During the outage `tsh` and the web terminal fail; the cluster itself stays up.
- After the second scenario the identity file is refused as expired, then works again.

## If it does not work

- `unknown certificate authority`, or you want a fresh Teleport: [Keep or reset the Teleport cluster](../manual/04-nodes-and-shells.md#keep-or-reset-the-teleport-cluster).

## Next

- What each user tier may do, and the audit trail: [Kubernetes through Teleport](../guides/teleport-kube.md).
