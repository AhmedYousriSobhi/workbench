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

## What a user may do: three tiers

Every user in `sim.yaml` has the base role `workbench-user` (SSH). With the k8s part each also has one Kubernetes tier role (all v8, ADR 0084):

| Tier | Teleport role | Kubernetes group | May |
|---|---|---|---|
| viewer (default) | `workbench-kube-viewer` | `workbench-k8s-viewers` | read nodes, pods, events cluster-wide; every write is `forbidden` |
| developer | `workbench-kube-developer` | `workbench-k8s-developers` | read and write workloads in `team-a` only; no nodes, no secrets, nothing in `team-b` |
| admin | `workbench-kube-admin` | `workbench-k8s-admins` | everything (`cluster-admin`) |

Pick a tier per user in `sim.yaml`, then `benchctl up` again (an existing user keeps its password):

```yaml
teleport:
  kube_tier: {bob: developer, alice: admin}   # users not listed are viewers
```

Two layers say no, with different messages. Outside the role's `kubernetes_resources` (the developer reading nodes) Teleport refuses. Inside them but not allowed by the Kubernetes Role (the developer reading secrets in `team-a`) Kubernetes answers `forbidden`, naming the user and the resource. A Teleport user whose role has no `kubernetes_groups` is denied.

Run live on 2026-11-19 (tsh 18.11.2 against Teleport 18.11.8): `tsh kube credentials` with the identity file works (it is what `kubectl` runs through the kubeconfig `tsh kube login` writes). `tsh kubectl` and `tsh proxy kube` do **not** work with only an identity file: kubectl gets no credentials and falls back to `localhost:8080`. Use `tsh kube login` and kubectl instead:

```bash
tsh --insecure -i compose/generated/secrets/teleport/identity/alice kube credentials --kube-cluster=workbench-k8s --teleport-cluster=workbench
```

## Failures to play

Each has a restore step (`./scripts/benchctl scenario run NAME`), and a different message:

| Scenario | What a user sees through Teleport |
|---|---|
| `kube-binding-missing` | Teleport lets the user in; Kubernetes answers `forbidden` naming the user (the tier's binding is gone) |
| `kube-impersonation-revoked` | every request fails upstream with `cannot impersonate` (whatever the tier) |
| `kube-identity-expired` | `tsh kube login` and `tsh kube credentials` fail: the identity file has expired |
| `k8s-cert-expired` | the API server's certificate has expired: an x509 error from Teleport's upstream call |

Written and unit-tested; the live run is owner-run, so the exact messages are not yet shown here.

## In the page

With the `teleport` and `k8s` parts built, click the Teleport box in the Infrastructure architecture:

- The details show Teleport's Kubernetes request, exec-session and port-forward counters. A counter Teleport does not publish reads `not measured`, never 0.
- They also list the last ten `kube.request` calls: user, verb, resource and the code with a word (ok, refused, error).
- With Teleport not answering, one line says so and gives `./scripts/benchctl up --with teleport --with k8s`.
- The page reads Teleport's diagnostics port and the audit files in `compose/generated/teleport/data/log`, read-only, with no Docker.
- Written and unit-tested; the live run is owner-run.

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
