# 6. Scenarios and faults

A scenario is a named, repeatable timeline of faults. They live in `scenarios/*.yaml`.

## Run one

```bash
./scripts/benchctl scenario list                          # reference (SC-nn), name, step count, description
./scripts/benchctl scenario run node-down --dry-run       # validate and print the timeline, change nothing
./scripts/benchctl scenario run node-down --speed 10      # play it ten times faster
./scripts/benchctl scenario run slurmd-restart-mid-job --trace   # after each step: job states and the new Slurm log lines
./scripts/benchctl scenario run SC-02 --dry-run          # the reference works instead of the name
```

The command exits 1 when a step errors or an `expect` line does not hold. Steps for parts that are not built (ticket-bot) are reported as **skipped**, not failed. From the web page ([chapter 5](05-web-page.md)) the `slurm` steps go through the governor; tick **Slurm trace** there for the same trace (see the [resilience guide](../guides/slurm-resilience.md)).

## The scenarios shipped

| Ref | Name | What happens |
|---|---|---|
| SC-17 | `node-down` | a compute node loses power; BMC and BCM show it down, Slurm drains it, then it recovers |
| SC-05 | `fan-failure` | fans fail on a GPU node and it runs hot; the BMC event log shows critical entries |
| SC-07 | `ib-port-down` | an InfiniBand port goes down on `gpu001`, `gpu002`'s NIC flaps; the health check drains the nodes, tickets open, then both recover |
| SC-01 | `client-stress` | five minutes of everything at once (job floods, GPU errors, heat, a node off and drained, 12 tickets, Jira mock 429/slow/503); run it while the client is open and measure the client ([how](../guides/client-testing.md)) |
| SC-02 | `client-stress-real` | `client-stress` for a client connected to the **real** Jira: the same nodes, GPUs, heat and jobs, with Jira's own rate limit (about 75 to 180 s), a CAPTCHA lock and a 30 s hang instead of the mock's faults, and the 12-ticket wave filed on the real Jira (needs `jira-real` up) |
| SC-06 | `gpu-xid-storm` | GPU errors on both GPU nodes, thermal rise, Slurm drain; the app should open tickets |
| SC-08 | `jira-429` | Jira rate-limits the next 5 calls (429 with Retry-After); the app must back off |
| SC-10 | `jira-outage` | Jira answers 503 to the next 20 calls, then recovers |
| SC-09 | `jira-flap` | Jira is refused, rate limited and unavailable in turn, each time healthy again, with tickets before and after |
| SC-13 | `jira-real-outage` | the **real** Jira (`jira-real`, part must be running) hangs for 60 s (container paused, calls time out), then answers again |
| SC-11 | `jira-real-429` | the real Jira's own rate limiting (3 requests, refill 1 per second) turns on, so bursts get a real 429 with `X-RateLimit-*` headers, then off at 120 s; each change shows up to a minute late |
| SC-28 | `jira-real-slow-503` | the real Jira answers slowly (8 s), then 503, then 429 with `Retry-After: 30`, through the fault proxy: **point the client at `127.0.0.1:8084`** |
| SC-29 | `jira-real-token-revoked` | alice's personal access token is revoked (a real 401 for a client using it), then a new one is written to `jira-real.env` at 90 s |
| SC-37 | `preempt-burst` | a scavenger job fills the GPUs of `gpu001`; at 20 s an `urgent` job asks for them and Slurm requeues the scavenger job; needs the accounting part and `qos:` in `sim.yaml` ([guide](../guides/qos-and-preemption.md)) |
| SC-36 | `reboot-asap` | Slurm reboots `cn002` (`scontrol reboot ASAP`): BMC `Reset ForceOff` then `Reset On`, slurmd restarts, the node resumes; then `cn003` is powered down through Slurm (`POWERED_DOWN`, BMC Off) and up at 90 s ([guide](../guides/power-and-reboot.md)) |
| SC-35 | `switch-leaf-down` | one leaf switch goes down: exactly its nodes drain (`switch leaf1 down`), and only those resume at 90 s; needs `topology:` in `sim.yaml` ([guide](../guides/topology.md)) |
| SC-30 | `auth-slurm-token-expiry` | a Slurm REST token for alice that lives 60 s is saved to `compose/generated/secrets/slurm_token`; after that slurmrestd answers 401 |
| SC-31 | `auth-teleport-cert-expiry` | alice's Teleport identity file lives 60 s, then the login node refuses it as expired; signed again at 180 s (`--with teleport`) |
| SC-32 | `auth-munge-wrong-key` | `cn001` gets another munge key (slurmctld: `Invalid credential`, node goes down), its own key is back at 120 s |
| SC-33 | `bmc-tls-expired` | the BMC farm presents a certificate that ended yesterday for 60 s: `certificate has expired` on 8443, 443 and each node's BMC address |
| SC-34 | `k8s-cert-expired` | the API server serves a certificate that ended yesterday for 90 s (`--with k8s`): `certificate has expired` from kubectl |
| SC-12 | `jira-real-captcha` | six wrong-password logins make the real Jira answer 401 then 403 `CAPTCHA_CHALLENGE` (what an integration meets after a bad password), cleared after 60 s |
| SC-26 | `ticket-lifecycle` | one ticket: Open, In Progress, a note, Resolved, Closed |
| SC-04 | `drain-ticket` | Slurm drains `cn003`; a ticket naming the cluster and the node is filed, worked, and resolved when the node resumes |
| SC-27 | `ticket-wave` | twelve tickets at once in every status and priority (see the [tickets guide](../guides/jira-tickets.md)) |
| SC-22 | `ssh-host-key-changed` | the login node presents a different host key for 60 s; a client that pinned the old one must refuse it |
| SC-15 | `login-node-down` | the login node refuses connections for 30 s, then returns with the same host key |
| SC-16 | `login-node-hung` | the login node freezes (connections time out, nothing refuses) for 30 s |
| SC-25 | `ssh-session-limit` | one session per connection for 60 s: a client opening several channels is refused the extra ones |
| SC-23 | `ssh-key-refused` | the login node rejects the client's key for 60 s: `Permission denied (publickey)` |
| SC-24 | `ssh-node-sshd-down` | sshd on `cn001` stops for 60 s: the hop `ssh cn001` is refused, the login node and other nodes work |
| SC-21 | `slurmd-restart-mid-job` | `slurmd` restarts under a running job; does `slurmstepd` survive ([guide](../guides/slurm-resilience.md); use `--trace`) |
| SC-20 | `slurmd-disabled-mid-job` | `slurmd` stays down 45 s under a running job, then returns |
| SC-19 | `slurmctld-restart-mid-job` | `slurmctld` restarts with a real and a simulated GPU job running |
| SC-03 | `drain-mid-job` | a node is drained under a running job; the job finishes, the node ends drained |
| SC-14 | `k8s-node-notready` | a Kubernetes GPU node reports NotReady, then recovers (needs the `k8s` part) |
| SC-18 | `pod-crashloop` | a pod crash-loops and another is OOMKilled (needs the `k8s` part) |

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
- `target`: `bmc-farm`, `bcm-mock`, `gpu-exporter`, `jira`, `slurm`, `ssh`, `k8s` (built); `ticket-bot` (not built, skipped).
- `do`: the body for that target. `nodes: [...]` fans out one request per node and must name nodes from `sim.yaml`. `slurm`
  takes `drain` and `resume` (node lists) and the job and daemon steps of the [resilience guide](../guides/slurm-resilience.md) (`submit: {node, gpus, duration}`, `cancel`, `slurmd_restart`, `slurmd_stop`, `slurmd_start`, `slurmctld_restart`); `jira` takes `failure: {mode: "429"|"503"|null, count: N}`; `bmc-farm` takes `power`,
  `temp`, `fan_fail`, `ramp: {to, over}`, `clear`; `ssh` (the login node) takes `state: down|hung|up` (stop, freeze, restore),
  `rotate_host_key: true`, `max_sessions: N` (OpenSSH MaxSessions) and `reset: true` (original key, default limit, node up).
- `k8s` (needs the part and Docker; the page skips it) takes `down: [node]` (the node reports NotReady, `KubeletNotReady`) and `up: [node]` (Ready again), `cordon` and `uncordon` (node lists), `pod: {namespace, name, fault: crashloop|oom|imagepull|jobfail, gpus: N}`,
  `delete_pod: {namespace, name}`, `scale: {namespace, deploy, replicas}` and `apply: [path under k8s/]`. Expectations: `k8s.node_ready: {node, ready, within}`
  and `k8s.pod_status: {namespace, name, reason, within}` (the pod's phase or a container's waiting or terminated reason).
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
