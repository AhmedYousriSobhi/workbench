# Recipe: gpu-fault-to-alert

## Goal
The GPUs of both GPU nodes fall off the bus and the health check drains them; check the app raises its alert and ticket.

## Needs
Profile `min` plus `--with jira` if the app files tickets. The workbench's Prometheus loads seven default rules (`GpuXidCritical`, `GpuTempHigh`, `InfinibandPortDown`, `NodeDrained`, `NodeDown`, `ExporterDown`, `JiraErrors`; `observability/rules.yml`), so `wait alert` and `get alerts` work out of the box. They only prove the faults are visible in metrics: the app's own alerting is what is under test.

## Steps
```bash
./scripts/benchctl start
./scripts/benchctl wait ready --timeout 180
./scripts/benchctl scenario run gpu-xid-storm --json > /tmp/gpu-xid-storm.json &
./scripts/benchctl wait alert GpuXidCritical --timeout 120
./scripts/benchctl get faults --json
./scripts/benchctl get alerts --json
wait
```
The 3 minute scenario injects only the cause (the fault model's `gpu-fell-off-bus`: XID 79 on both GPU nodes and a critical BMC event); the node health check drains each node within about 30 s and a ticket is filed, which the scenario asserts. It then clears the fault.

## What your app should show
GPU error XID 79 on both nodes, both drained, and (if it files tickets) an issue for each node.

## Assert
`get faults --json` lists `gpu001` and `gpu002` while it plays. The scenario's `jira.issue_created` expectation is met by the workbench's own ticket (label `fault-xid`), so it needs the Jira mock (`--with jira`), not the app. `wait alert GpuXidCritical` returns about 15 s after the XID is set (the rule holds for 15 s, Prometheus scrapes every 15 s); `wait alert GpuXidCritical --resolved` returns after the scenario's clear. This scenario has no thermal ramp, so `GpuTempHigh` does not fire here; it fires when the exporter's own temperature is set above 85 C (a `gpu-exporter` step with `temp`). The scenario's own report is the JSON on stdout.

## Clean up
```bash
./scripts/benchctl stop
```

## Memory cost
`min`: 0.5 GB expected, 1.5 GB with the app reserve (`docs/guides/memory.md`).
