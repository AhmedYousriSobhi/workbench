# Recipe: drain-to-ticket

## Goal
A node drains in Slurm and the app files, updates and resolves a ticket for it.

## Needs
Profile `min` (it includes the Jira mock). Scenario `drain-ticket` uses `cn003`.

## Steps
```bash
./scripts/benchctl start
./scripts/benchctl wait ready --timeout 180
./scripts/benchctl scenario run drain-ticket --json
./scripts/benchctl get traffic --json
```
The scenario drains `cn003` and plays the ticket moves itself through the Jira mock (create, In Progress, comment, resolve) so you can compare your app's view with it.

## What your app should show
`cn003` drained for "failing disk", then resumed; the ticket naming the cluster and node moving through its states.

## Assert
The scenario result has `"passed": true`. `get traffic --json` shows which calls your app made to `jira-mock` and how they ended.

## Clean up
```bash
./scripts/benchctl stop
```

## Memory cost
`min`: 0.5 GB expected, 1.5 GB with the app reserve (`docs/guides/memory.md`).
