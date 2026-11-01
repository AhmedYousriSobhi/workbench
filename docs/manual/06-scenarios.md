# 6. Scenarios and faults

A scenario is a named, repeatable timeline of faults. They live in `scenarios/*.yaml`.

## Run one

```bash
./scripts/benchctl scenario list                          # name, step count, description
./scripts/benchctl scenario run node-down --dry-run       # validate and print the timeline, change nothing
./scripts/benchctl scenario run node-down --speed 10      # play it ten times faster
./scripts/benchctl scenario run slurmd-restart-mid-job --trace   # after each step: job states and the new Slurm log lines
```

The command exits 1 when a step errors or an `expect` line does not hold. Steps for parts that are not built (Kubernetes,
ticket-bot) are reported as **skipped**, not failed. From the web page ([chapter 5](05-web-page.md)) the `slurm` steps go through the governor; tick **Slurm trace** there for the same trace (see the [resilience guide](../guides/slurm-resilience.md)).

## The scenarios shipped

| Name | What happens |
|---|---|
| `node-down` | a compute node loses power; BMC and BCM show it down, Slurm drains it, then it recovers |
| `fan-failure` | fans fail on a GPU node and it runs hot; the BMC event log shows critical entries |
| `ib-port-down` | an InfiniBand port goes down on `gpu001`, `gpu002`'s NIC flaps; the health check drains the nodes, tickets open, then both recover |
| `gpu-xid-storm` | GPU errors on both GPU nodes, thermal rise, Slurm drain; the app should open tickets |
| `jira-429` | Jira rate-limits the next 5 calls (429 with Retry-After); the app must back off |
| `jira-outage` | Jira answers 503 to the next 20 calls, then recovers |
| `jira-flap` | Jira is refused, rate limited and unavailable in turn, each time healthy again, with tickets before and after |
| `ticket-lifecycle` | one ticket: Open, In Progress, a note, Resolved, Closed |
| `drain-ticket` | Slurm drains `cn003`; a ticket naming the cluster and the node is filed, worked, and resolved when the node resumes |
| `ticket-wave` | twelve tickets at once in every status and priority (see the [tickets guide](../guides/jira-tickets.md)) |
| `ssh-host-key-changed` | the login node presents a different host key for 60 s; a client that pinned the old one must refuse it |
| `login-node-down` | the login node refuses connections for 30 s, then returns with the same host key |
| `login-node-hung` | the login node freezes (connections time out, nothing refuses) for 30 s |
| `ssh-session-limit` | one session per connection for 60 s: a client opening several channels is refused the extra ones |
| `ssh-key-refused` | the login node rejects the client's key for 60 s: `Permission denied (publickey)` |
| `ssh-node-sshd-down` | sshd on `cn001` stops for 60 s: the hop `ssh cn001` is refused, the login node and other nodes work |
| `slurmd-restart-mid-job` | `slurmd` restarts under a running job; does `slurmstepd` survive ([guide](../guides/slurm-resilience.md); use `--trace`) |
| `slurmd-disabled-mid-job` | `slurmd` stays down 45 s under a running job, then returns |
| `slurmctld-restart-mid-job` | `slurmctld` restarts with a real and a simulated GPU job running |
| `drain-mid-job` | a node is drained under a running job; the job finishes, the node ends drained |
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
- `target`: `bmc-farm`, `bcm-mock`, `gpu-exporter`, `jira`, `slurm`, `ssh` (built); `k8s`, `ticket-bot` (not built, skipped).
- `do`: the body for that target. `nodes: [...]` fans out one request per node and must name nodes from `sim.yaml`. `slurm`
  takes `drain` and `resume` (node lists) and the job and daemon steps of the [resilience guide](../guides/slurm-resilience.md) (`submit: {node, gpus, duration}`, `cancel`, `slurmd_restart`, `slurmd_stop`, `slurmd_start`, `slurmctld_restart`); `jira` takes `failure: {mode: "429"|"503"|null, count: N}`; `bmc-farm` takes `power`,
  `temp`, `fan_fail`, `ramp: {to, over}`, `clear`; `ssh` (the login node) takes `state: down|hung|up` (stop, freeze, restore),
  `rotate_host_key: true`, `max_sessions: N` (OpenSSH MaxSessions) and `reset: true` (original key, default limit, node up).
- `expect`: assertions checked after the steps. Anything that needs the app under test (such as `jira.issue_created`) only
  holds when the app, or ticket-bot once built, does its part.

Always run `--dry-run` first: it rejects unknown targets and unknown nodes. Design: [ADR 0006](../adr/0006-scenario-engine-runs-in-the-host-cli.md).

## Faults reach the whole cluster

A `bmc-farm` step with `power: "Off"` is a power loss for the node: besides the BMC it sets the BCM state `DOWN`, makes the GPU
exporter stop publishing the node and freezes the node's container, so `slurmctld` marks it `down*` (Not responding) by
itself after about 30-40 s. `power: "On"` and `clear: true` undo all of that. You write one step; the BCM, exporter and
Slurm steps are no longer needed (they are harmless if present). Played from the page, the Slurm part (`drain`, `down`,
`resume`, `power_off`, `power_on`) goes through the governor, at calm levels only (L0-L1); when it is refused the step still
runs and says why in its detail. `ssh` steps still need `benchctl scenario run`.
