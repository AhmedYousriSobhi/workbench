# ADR 0056: Network faults: userspace proxies by default, netem only through the root service

Status: accepted (owner decision on issue 478; wording for issue 471, C-12). Built: the proxies for the real Jira (issue 305) and the root-service netem verb (issue 478, addendum below).

## Context
SPEC C-12 asked for `tc netem` on the Docker bridge networks and named Toxiproxy. `tc` needs root, and starting the simulation must not need sudo (NF-5, ADR 0010). The client talks to the login node over SSH and to HTTP services; a userspace proxy can slow all of those. netem matters only for links inside the cluster (slurmd to slurmctld), which the freeze-based resilience scenarios already approximate. The repository uses its own stdlib proxy (`benchctl/jira_proxy.py`), not Toxiproxy.

## Decision
- Client-facing network faults (latency, errors, outage) are userspace proxies. No root, no new dependency.
- Faults inside the cluster use `tc netem` only through the optional root governor service, with these conditions:
  - Bounded targets: the project's own networks or container veths only, no free-form interface names; the socket is group-writable (W-3), so every value is validated.
  - Bounded values: delay and loss have upper limits.
  - A TTL on every fault; the governor clears every netem rule at startup, so a crashed scenario leaves nothing behind.
  - Allowed at governor levels L0 and L1 only, like `scale` (`tc` forks a process).
- Without the root service the netem verb is refused with one message that says so.

## Consequences
- Every root verb widens the attack surface of a memory-safety tool; this one is allow-listed and validated.
- A user without the root service loses only in-cluster link faults.
- SPEC C-12 follows this text; the netem verb is tracked in issue 478.

## Addendum (issue 478): the netem verb as built

Choices the ADR left open, made conservatively:

- **Where tc runs.** Inside the target container's network namespace (`nsenter -t PID -n tc qdisc replace dev ethN root netem ...`), on its `ethN` devices only. No host veth, no bridge, no free-form interface name. The rule dies with the container.
- **Targets.** A running Slurm node container of this project: name `workbench-NODE-1`, labels `workbench.node` and compose project `workbench`, not the host network, node name matching the sim.yaml pattern. Not the controller, login, mocks or Kubernetes.
- **Bounds.** Delay 1 to 500 ms, loss 0.1 to 20 %, TTL 1 s to 10 min (60 s when not given). No jitter, correlation, reordering or total partition (a partition is the freeze scenarios).
- **Gate.** `netem-set` only at L0-L1; `netem-clear` at every level (it only removes load).
- **Cleanup.** TTL timer in the governor, `netem-clear`, every rule removed at governor start and at exit (only netem root qdiscs on `ethN`; other qdiscs are left alone).
- **Root only.** The governor builds the netem runner only when it is root, not `--user-mode` and not `--dry-run`; otherwise every netem request answers one message naming `sudo ./scripts/install-governor.sh`.
- **Never the page.** Governor commands `netem-set` and `netem-clear`, CLI `benchctl set|clear netem`, scenario target `netem` (a host-CLI target the page skips). The page has no button and the web UI module does not call them (W-3).

See [network faults](../guides/network-faults.md). The live test is `tests/real/test_netem_root.py`, run as root; it was not run when this was written.
