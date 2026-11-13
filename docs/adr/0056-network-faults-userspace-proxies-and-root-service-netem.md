# ADR 0056: Network faults: userspace proxies by default, netem only through the root service

Status: accepted (owner decision on issue 478; wording for issue 471, C-12). Not built: the proxies exist for the real Jira (issue 305), the root-service netem verb does not.

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
