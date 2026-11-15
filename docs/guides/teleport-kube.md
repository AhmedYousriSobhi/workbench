# Kubernetes through Teleport (`tsh kube`)

With both the `teleport` and the `k8s` part on, Teleport's Kubernetes Service (inside the existing Teleport container) registers the simulated
cluster, so `tsh kube` and `kubectl` through the Teleport proxy work. Same port as everything else (3080); nothing new to publish (ADR 0083, issue 383).

Status: proven live (`./scripts/verify.sh --unit teleport_kube`, 5 passed): the cluster registers, a viewer reads the nodes, a write is refused, and a user without a Kubernetes group is denied.

## Start it

```bash
./scripts/benchctl up --with teleport --with k8s
```

`up` prints the working `tsh kube login` line. Turning either part on or off later recreates the Teleport container (it reads its configuration only at start).

## Use it

The proxy certificate is self-signed, so every `tsh` call carries `--insecure`. An identity file needs no password or one-time code.

```bash
export TELEPORT_HOME=$(mktemp -d)           # keeps your own ~/.tsh untouched
export KUBECONFIG=$TELEPORT_HOME/kubeconfig
export TELEPORT_IDENTITY_FILE=compose/generated/secrets/teleport/identity/alice
export TELEPORT_PROXY=10.141.255.30:3080
tsh --insecure kube ls
tsh --insecure kube login workbench-k8s
kubectl get nodes
```

The cluster is named after the kubeconfig context, `workbench-k8s`. kubectl calls `tsh kube credentials` for each request, so `TELEPORT_IDENTITY_FILE` and `TELEPORT_PROXY` must be set in the shell where you run kubectl; without them kubectl fails with "no SSH auth methods loaded".

## What a user may do

Every user in `sim.yaml` has the Teleport role `workbench-user`. With the k8s part it grants the Kubernetes group `workbench-k8s-viewers`, bound to the
read-only `workbench-viewer` role: `kubectl get nodes|pods|events ...` works, `kubectl create|delete ...` is refused by Kubernetes RBAC (`forbidden`).
A Teleport user whose role has no `kubernetes_groups` is denied.

## How it is wired

- `teleport.yaml` gets a `kubernetes_service` reading `compose/generated/secrets/teleport/kube.kubeconfig` (generated, mode 600, never tracked).
- That kubeconfig uses the token user `teleport`, which may only impersonate (`k8s/fixtures/47-rbac-teleport.yaml`); it never uses the admin certificate.
- Without the k8s part there is no `kubernetes_service` and no kubeconfig.
