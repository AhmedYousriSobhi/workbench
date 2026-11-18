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

## Audit: who ran which kubectl call

Teleport writes one JSON line per audit event, one file per day, under `/var/lib/teleport/log/` in the container; on the host that folder is `compose/generated/teleport/data/log/` (gitignored). Every kubectl call through the proxy is a `kube.request` event (code `T3009I`) with the Teleport user, so you can see who read or tried to change what:

```bash
docker exec workbench-teleport-1 sh -c "find /var/lib/teleport/log -name '*.log' -exec cat {} +" | grep '"kube.request"' | tail -3
```

The fields Teleport documents for it: `user`, `kubernetes_cluster`, `verb`, `request_path`, `resource_api_group`, `resource_kind`, `resource_name`, `resource_namespace`, `response_code`, `login`, `addr.*`, `server_id`, `time`.

- A read (`kubectl get node kcn001`) is `verb` `GET` with `response_code` 200.
- A refused write (`kubectl delete pod x -n team-a`) is `DELETE` with 403: Teleport forwards it and Kubernetes RBAC refuses it.
- The live tests assert both for `alice` (`./scripts/verify.sh --unit teleport_kube`, helpers in `benchctl/teleportaudit.py`) and fail with a clear message if the folder is missing.

Status of this section:

- The field list is from Teleport's audit event reference, not yet from the pinned 18.11.8.
- The live test prints the fields 18.11.8 really writes; the live run is owner-run, so no real line is shown here yet.
- For a user without a Kubernetes group the test records what is written and asserts only that no 200 is audited for that user.
- `exec`, `port` and `session.*` events are not expected: nothing here opens a pod shell.
