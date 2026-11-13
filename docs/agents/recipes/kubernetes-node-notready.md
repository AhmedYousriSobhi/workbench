# Recipe: kubernetes-node-notready

## Goal
A Kubernetes GPU node goes NotReady, then recovers; check the app alerts.

## Needs
The optional Kubernetes part: `benchctl start --workload kubernetes` (about 0.3 GB measured, see `docs/manual/04-nodes-and-shells.md`). Scenario `k8s-node-notready` uses `kgpu001`.

## Steps
```bash
./scripts/benchctl start --workload kubernetes
./scripts/benchctl wait ready --timeout 240
./scripts/benchctl scenario run k8s-node-notready --json
./scripts/benchctl get faults --json
```
The node is NotReady for 120 s. Run `get faults --json` from a second shell while it plays.

## What your app should show
`kgpu001` NotReady (KubeletNotReady) within 90 s, then Ready again.

## Assert
The scenario's `k8s.node_ready` expectation is `ok` and the result has `"passed": true`.

## Clean up
```bash
./scripts/benchctl stop
```

## Memory cost
`min` 0.5 GB expected plus about 0.3 GB for Kubernetes, with the 1 GB app reserve: run `benchctl plan --workload kubernetes --json` for the exact figure.
