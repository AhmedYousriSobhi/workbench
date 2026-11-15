# Kubernetes through Teleport (`tsh kube`)

With both the `teleport` and the `k8s` part on, Teleport's Kubernetes Service (inside the existing Teleport container) registers the simulated
cluster, so `tsh kube` and `kubectl` through the Teleport proxy work. Same port as everything else (3080); nothing new to publish (ADR 0083, issue 383).

Status: written and unit-tested; the commands below follow Teleport's documentation and are to be proven by `./scripts/verify.sh --unit teleport_kube`.
Where a step was not yet proven live it says so.

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
ID=compose/generated/secrets/teleport/identity/alice
tsh --proxy=10.141.255.30:3080 --insecure -i $ID kube ls
tsh --proxy=10.141.255.30:3080 --insecure -i $ID kube login workbench-k8s
kubectl get nodes
```

(The cluster is named after the kubeconfig context, `workbench-k8s`; `tsh kube login` with an identity file is not yet proven live.)

## What a user may do

Every user in `sim.yaml` has the Teleport role `workbench-user`. With the k8s part it grants the Kubernetes group `workbench-k8s-viewers`, bound to the
read-only `workbench-viewer` role: `kubectl get nodes|pods|events ...` works, `kubectl create|delete ...` is refused by Kubernetes RBAC (`forbidden`).
A Teleport user whose role has no `kubernetes_groups` is denied.

## How it is wired

- `teleport.yaml` gets a `kubernetes_service` reading `compose/generated/secrets/teleport/kube.kubeconfig` (generated, mode 600, never tracked).
- That kubeconfig uses the token user `teleport`, which may only impersonate (`k8s/fixtures/47-rbac-teleport.yaml`); it never uses the admin certificate.
- Without the k8s part there is no `kubernetes_service` and no kubeconfig.
