# Recipe: gpu-fault-to-alert

## Goal
GPU errors on both GPU nodes, a thermal rise and a drain; check the app raises its alert and ticket.

## Needs
Profile `min` plus `--with jira` if the app files tickets. The workbench ships **no Prometheus alert rules**: `wait alert` works only after you load a rule into the workbench's Prometheus. The app's own alerting is what is under test.

## Steps
```bash
./scripts/benchctl start
./scripts/benchctl wait ready --timeout 180
./scripts/benchctl scenario run gpu-xid-storm --json
./scripts/benchctl get faults --json
./scripts/benchctl get alerts --json
```
The 3 minute scenario sets XID 79 on `gpu001` and `gpu002`, ramps `gpu001` to 92 C, drains it at 90 s, then clears everything.

## What your app should show
GPU error XID 79 on both nodes, `gpu001` overheating and drained, and (if it files tickets) an issue labelled `gpu` and `xid` within 3 minutes.

## Assert
`get faults --json` lists `gpu001` and `gpu002` while it plays. The scenario's `jira.issue_created` expectation needs the app to file the ticket; it is `skipped` or `failed` otherwise. With a rule loaded: `./scripts/benchctl wait alert NAME --timeout 240`.

## Clean up
```bash
./scripts/benchctl stop
```

## Memory cost
`min`: 0.5 GB expected, 1.5 GB with the app reserve (`docs/guides/memory.md`).
