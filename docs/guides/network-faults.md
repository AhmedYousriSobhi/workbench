# Network faults: slow or lossy links inside the cluster

`tc netem` adds delay and packet loss to one Slurm node's own network (for example `slurmd` to `slurmctld`). It exists **only through the optional root governor service** ([ADR 0056](../adr/0056-network-faults-userspace-proxies-and-root-service-netem.md), SPEC C-12). Faults on the paths a client uses (the real Jira, HTTP, SSH) use userspace proxies instead and need no root ([jira-real](jira-real.md)).

## Before you start

1. The root service must run: `sudo ./scripts/install-governor.sh` (once; nothing else in workbench needs sudo). The rootless governor that `benchctl start` launches refuses netem with `netem needs the root governor service ... install it with sudo ./scripts/install-governor.sh`.
2. The cluster is up and the governor is at memory level L0 or L1. A fault is refused at L2 and above (`paused: memory pressure`); clearing is allowed at every level.
3. `tc` (iproute2) and `nsenter` (util-linux) are installed on the host (the default on Ubuntu).

## Set and clear

```bash
./scripts/benchctl set netem cn001 --delay 200ms --loss 5% --ttl 60s
./scripts/benchctl clear netem cn001      # early; no node name clears every node
./scripts/benchctl clear netem
```

Either `--delay` or `--loss` (or both) is needed. `--ttl` defaults to 60 s: **every fault ends by itself**.

| Value | Allowed |
|---|---|
| `--delay` | 1ms to 500ms |
| `--loss` | 0.1% to 20% |
| `--ttl` | 1s to 10m |
| target | a Slurm node container of this cluster (`cn001`, `gpu001`); nothing else |

A value outside the range, a node that is not a container of this cluster, or a missing delay and loss each print one message and change nothing. The governor's `status` reply (the `netem` field) lists active faults and their time left, and its audit log records each set and clear.

## In a scenario

```yaml
- {at: 0s,  target: netem, do: {nodes: [cn001], delay: 200ms, loss: 5%, ttl: 60s}}
- {at: 90s, target: netem, do: {clear: true}}
```

`scenarios/netem-slow-link.yaml` (SC-45) is this. Run it with `./scripts/benchctl scenario run netem-slow-link`. `scenario run --dry-run` and validation check the values without a governor. The page's scenario player **skips** the step ("needs the host CLI"): netem is CLI and scenarios only, so the root verb is never on the web path (SPEC W-3).

## What it does, and what keeps it safe

- tc runs inside the node container's own network namespace (`nsenter -t PID -n tc qdisc replace dev ethN root netem ...`) on its `ethN` devices. No host interface and no Docker bridge is touched, and the rule disappears with the container.
- The control socket is group-writable, so the governor validates every request again (node name pattern, number ranges) and checks `docker inspect`: the container is `workbench-NODE-1`, has the compose project label `workbench` and the `workbench.node` label, and is not on the host network.
- The governor removes every netem rule when it starts and when it exits, so a scenario that crashed leaves nothing. An installed service restart is the reset button.

## Troubleshooting

| Message | Meaning |
|---|---|
| `netem needs the root governor service` | only the user-mode governor is running: `sudo ./scripts/install-governor.sh` |
| `this governor has no netem; reinstall the root service` | the installed service is older than this version |
| `paused: memory pressure (L2); netem starts only at L0-L1` | free memory first, try again |
| `no container workbench-cn001-1` | the node is not running, or is not one of this cluster's |
| `tc failed on cn001 eth0: ...` | the kernel refused the qdisc; the half-applied rule is removed |

The root test is `tests/real/test_netem_root.py`, run with `sudo ./scripts/real-test-root.sh`.
