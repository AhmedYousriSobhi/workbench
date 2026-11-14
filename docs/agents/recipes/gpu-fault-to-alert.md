# Recipe: gpu-fault-to-alert

## Goal
GPU errors on both GPU nodes, a thermal rise and a drain; check the app raises its alert and ticket.

## Needs
Profile `min` plus `--with jira` if the app files tickets. The workbench's Prometheus loads six default rules (`GpuXidCritical`, `GpuTempHigh`, `InfinibandPortDown`, `NodeDown`, `ExporterDown`, `JiraErrors`; `observability/rules.yml`), so `wait alert` and `get alerts` work out of the box. They only prove the faults are visible in metrics: the app's own alerting is what is under test.

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
The 3 minute scenario sets XID 79 on `gpu001` and `gpu002`, ramps `gpu001` to 92 C, drains it at 90 s, then clears everything.

## What your app should show
GPU error XID 79 on both nodes, `gpu001` overheating and drained, and (if it files tickets) an issue labelled `gpu` and `xid` within 3 minutes.

## Assert
`get faults --json` lists `gpu001` and `gpu002` while it plays. The scenario's `jira.issue_created` expectation needs the app to file the ticket; it is `skipped` or `failed` otherwise. `wait alert GpuXidCritical` returns about 15 s after the XID is set (the rule holds for 15 s, Prometheus scrapes every 15 s); `wait alert GpuXidCritical --resolved` returns after the scenario's clear. This scenario's thermal ramp is on the BMC, not on the GPU metrics, so `GpuTempHigh` does not fire here; it fires when the exporter's own temperature is set above 85 C (a `gpu-exporter` step with `temp`). The scenario's own report is the JSON on stdout.

## Clean up
```bash
./scripts/benchctl stop
```

## Memory cost
`min`: 0.5 GB expected, 1.5 GB with the app reserve (`docs/guides/memory.md`).
