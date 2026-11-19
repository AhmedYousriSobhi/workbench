# ADR 0088: SSH traffic counters: a small counter inside each login and jump container

Status: **Accepted** (2026-11-19, owner decision: "go with the recommendation"). Issue 213. Builds on [ADR 0009](0009-traffic-dots-from-request-counters.md) (dots from request counters), [ADR 0011](0011-ssh-login-node.md), [ADR 0023](0023-login-nodes-and-their-auth.md) (login nodes and their auth), [ADR 0041](0041-per-request-traffic-record.md) (traffic analysis) and SPEC W-3 (the page never gets Docker or root).

## Context

The diagram draws dots for the mocks and for Teleport, because each of them counts what it serves. The direct SSH lines (client to login node) and the jump host line stay still: nothing counts SSH connections, and the page cannot look into a login or jump container (SPEC W-3: no Docker, no libvirt, only the governor socket). The owner asked for a dot on the line when a real `ssh` comes in, and for no dot when nothing was counted (nothing invented).

## Decision

**Option 1: a tiny counter inside each login and jump container, served on a localhost port that the page backend polls like the mocks' `GET /sim/stats`.**

- `images/login/ssh_counter.py` (Python standard library, one file). sshd logs to a file (`sshd -D -E /var/log/sshd.log`); the counter follows that file from its end, counts each `Accepted publickey ... from IP` line and serves `{"requests": N, "via_ca": M, "by_source": {IP: n}}` on `GET /sim/stats`. It repeats every line to its own stdout, so `docker logs` shows sshd as before.
- What is counted: an authenticated key login. A bare TCP connect (a liveness check, a port scan) never authenticates and is not counted, so the workbench's own probes (the page's `port_open`, `doctor`) are excluded by construction; the workbench opens no authenticated SSH session of its own. A refused login is not counted. A Teleport login (key type ending in `-CERT`) is kept apart as `via_ca`, because the Teleport counter already has it.
- What is kept: a number per source address, at most 64 addresses. Never a user name, a command or any content.
- The same file is mounted read-only into every login container and the jump container by compose (`COUNTER_MOUNT` in `benchctl/compose.py`). The login image already has Python (node-base). The jump image is a plain `ubuntu:24.04` with its own single apt layer; `python3` is added to that layer. It is not built on node-base, so the "OS packages only in node-base's apt layer" rule is not touched.
- Ports (one block, `benchctl/compose.py`): the counter listens on 9922 inside the container and is published on `127.0.0.1` only, at the container's published sshd port plus 100: the jump host 2320, the controller's 2321 stays free (the controller has no counter yet), the login nodes 2322 to 2329 (`MAX_LOGINS` is 8). A Teleport-only login publishes nothing and has no direct line.
- Memory: login 40 to 48 MB and jump 24 to 32 MB in `CATALOG`, an 8 MB estimate each until measured ([04-memory-and-profiles](../architecture/04-memory-and-profiles.md)). Class stays P2. No `cap_add`, no new privilege.
- The page backend (`webui/flow.py`): `ssh_sources()` reads the saved selection (as `saved_logins` does) and returns `ssh:NAME` for each login that takes a key and `ssh:jump` when the jump part is built. `Flow.poll` reads each counter like a mock's; the first read only baselines; a counter that does not answer gives no dots (it is shown as not reachable, as a mock is). A rise becomes an event `clients -> ssh:NAME` (label `connections`). A rise whose source address is the jump host's fixed address (`addressing.service_ip("jump")`) is the jump host's hop, an event `jump -> hop:NAME`, never a client dot.
- The diagram: the classic diagram registers each SSH line as a lane (`ln-ssh-NAME`, `ln-ssh-jump`, `ln-hop-NAME`), so the dots run along it and the line lights while sessions were seen in the last minute. The Circuit view (the default) lights the `clients-login`, `clients-klogin`, `clients-jump` and `jump-login` wires from the same counters and says "sessions a minute". `webui/graph.py` marks those edges measured. The controller's SSH line and the hops to nodes stay declared wiring: nothing counts them.

## Options considered

| Option | Why not chosen |
|---|---|
| (2) A governor verb that counts sshd connections with `docker exec`, polled by the page through the governor socket | It broadens the governor (a host process with Docker access) for a read the container can serve itself, and it runs at every poll while the governor is under memory pressure. It would also need a new verb, a socket route and tests in the safety-critical component. |
| (3) sshd log lines read by an exporter outside the container | An exporter (or `docker logs`) needs Docker access or a shared log volume, a parser to keep in step with sshd, and one more process to run and budget. The chosen counter also reads sshd's log lines, but from inside the container, in a process that is part of the node; the page gets numbers over a localhost port like every other counter, with no Docker access (W-3) and no new service. |

## Consequences

- The login and jump images change (a log file for sshd, one more process, `python3` in the jump image): they are rebuilt once. A cluster started before this change has no counter: its SSH lines stay still and say declared (no counter, no dots).
- sshd's output goes to a file the counter repeats. If the counter dies, sshd keeps running and `docker logs` stops showing sshd's lines, and the dots stop. `login.sh` starts the counter only when its file is mounted.
- The scenario `ssh` faults still work: `docker stop`/`start` restart the container (the counter starts again, the page re-baselines because the count fell); `kill -HUP` makes sshd re-execute with the same log file.
- Counts are per source address. Behind the published port every direct client has the Docker gateway's address, so "per source" tells a direct client from the jump host's hop and nothing finer.
- The Traffic panel's timeline and the Timeline dock show SSH events as sessions.
- Not covered: the controller's SSH (slurmctld's container has no counter), the hops from a login node to a compute node, and `tsh ssh` through Teleport (the Teleport counter has it).

## How to change this later

- **Switch to option 2 (governor verb):** add a verb to `governor/` (a read-only `ssh-counts` that runs `docker exec <container> ss -tn state established '( sport = :22 )'` or reads the sshd log, class P5, allowed at L0-L2); add the route in `webui/app.py` and call the governor socket from `webui/flow.py` instead of `http_json`; in `Flow.poll`, replace the `ssh` source URLs with that call. Delete `images/login/ssh_counter.py`, `COUNTER_MOUNT`, `COUNTER_OFFSET`, the counter lines in `images/login/login.sh` and `images/jump/entrypoint.sh`, the `python3` in `images/jump/Dockerfile`, and put the CATALOG limits back (40 and 24). Update `tests/test_ssh_counter.py` and `tests/real/test_ssh_counter_real.py`; the front end (`ssh:` and `hop:` keys) stays.
- **Switch to option 3 (exporter):** keep sshd on `-e` (revert `-E` in the two entrypoints), add an exporter component under `components/` (budget and class in `CATALOG`) that reads `docker logs` or a shared log volume and serves the same `{"requests", "by_source"}` document; point `ssh_sources()` in `webui/flow.py` at it; remove the in-container counter as above.
- **Count the controller too:** mount `COUNTER_MOUNT` into `slurmctld` in the controller block of `benchctl/compose.py`, start the counter in the node-base entrypoint when `WORKBENCH_SSHD` is set (sshd there starts from node-base), publish 2321, add `ssh:controller` in `ssh_sources()`, and add `ssh: 'ssh:controller'` to the `clients-controller` wire in `webui/static/js/circuitlayout.js` and an `sshLane` call for the controller line in `webui/static/js/diagram.js`.
- **Count failures too:** add an `errors` field in `ssh_counter.py` (sshd writes several lines per refused attempt, so pick one line to count) and `Flow` will draw triangles through the existing `errors` path.
