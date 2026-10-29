# 6. Scenarios and faults

A scenario is a named, repeatable timeline of faults. They live in `scenarios/*.yaml`.

## Run one

```bash
./scripts/simctl scenario list                          # name, step count, description
./scripts/simctl scenario run node-down --dry-run       # validate and print the timeline, change nothing
./scripts/simctl scenario run node-down --speed 10      # play it ten times faster
```

The command exits 1 when a step errors or an `expect` line does not hold. Steps for parts that are not built (Kubernetes,
ticket-bot) are reported as **skipped**, not failed. From the web page ([chapter 5](05-web-page.md)) the `slurm` steps are skipped.

## The scenarios shipped

| Name | What happens |
|---|---|
| `node-down` | a compute node loses power; BMC and BCM show it down, Slurm drains it, then it recovers |
| `fan-failure` | fans fail on a GPU node and it runs hot; the BMC event log shows critical entries |
| `gpu-xid-storm` | GPU errors on both GPU nodes, thermal rise, Slurm drain; the app should open tickets |
| `jira-429` | Jira rate-limits the next 5 calls (429 with Retry-After); the app must back off |
| `jira-outage` | Jira answers 503 to the next 20 calls, then recovers |
| `k8s-node-notready` | a KWOK node flips to NotReady (**Kubernetes not built**: skipped) |
| `pod-crashloop` | a pod crash-loops and another is OOMKilled (**Kubernetes not built**: skipped) |

## Write your own

```yaml
name: my-scenario
description: One line that says what the app should notice
steps:
  - {at: 0s,  target: bmc-farm, do: {nodes: [cn002], power: "Off"}}
  - {at: 5s,  target: slurm,    do: {drain: [cn002], reason: "node down"}}
  - {at: 40s, target: bmc-farm, do: {nodes: [cn002], clear: true}}
  - {at: 40s, target: slurm,    do: {resume: [cn002]}}
expect:
  - slurm.node_state: {node: cn002, state: drain, within: 20s}
```

- `at`: when the step runs, as `10s`, `2m`. `--speed` and `time_dilation` in `sim.yaml` compress it.
- `target`: `bmc-farm`, `bcm-mock`, `gpu-exporter`, `jira`, `slurm` (built); `k8s`, `ticket-bot` (not built, skipped).
- `do`: the body for that target. `nodes: [...]` fans out one request per node and must name nodes from `sim.yaml`. `slurm`
  takes `drain` and `resume` (node lists); `jira` takes `failure: {mode: "429"|"503"|null, count: N}`; `bmc-farm` takes `power`,
  `temp`, `fan_fail`, `ramp: {to, over}`, `clear`.
- `expect`: assertions checked after the steps. Anything that needs the app under test (such as `jira.issue_created`) only
  holds when the app, or ticket-bot once built, does its part.

Always run `--dry-run` first: it rejects unknown targets and unknown nodes. Design: [ADR 0006](../adr/0006-scenario-engine-runs-in-the-host-cli.md).
