# 0013. BCM for SSH-based clients is a fake `cmsh` on the login node

Status: accepted; built in issue 66

## Context
ADR 0005 gave bcm-mock a CMDaemon-style `POST /json` surface on an assumed call list. The one client we now know (a desktop monitoring app),
never calls it. Its BCM section reaches the head node through the cluster's login node (an SSH hop with its own
saved credentials), then runs `cmsh` there: `cmsh -c "device; list"` and `device; status` for the device table, other
fixed read-only scripts (categories, job queues, health checks), an interactive `cmsh` console, and a tail of the
CMDaemon events log. Nothing in that path is HTTP.

## Decision
- **Keep** bcm-mock's JSON API for any client that does use CMDaemon's JSON interface; it stays `partial` (ADR 0005).
- **Add** a fake `cmsh` to the login node (ADR 0011), implemented as a follow-up (issue 66): the login node doubles as
  the BCM head node. It answers the read-only queries above from `sim.yaml` and bcm-mock's node state, so a node taken
  down by a scenario shows as down in `device; status`. It refuses everything that would change state.
- Output follows real `cmsh` table formatting closely enough for a client's parser; the formats are an assumed subset
  until a real capture is available (record it as a fixture then, NF-2).
- The events-log tail and `module load cmsh` are out of scope for the first cut. The interactive console was added later (issue 144): `[host]% ` prompt, modes (`device`, `category`, `jobqueue`, `monitoring` > `healthchecks`), `list`, `status`, `show <name>`, `..`, `exit`, `help` (lists the commands of the current mode), Tab completion of commands and node names on a terminal, read-only like `-c`.

## Consequences
- No new container and no new port: the cost is one script in `images/login`.
- A client that expects a separate head-node host can be pointed at `login`.
- The BCM surface stays unverified against a real BCM; SPEC C-4 says so.

## As built
`images/login/cmsh.py` (Python 3 on the login image) calls bcm-mock's `POST /json` and prints `device; list` (head node row first,
then every node; Type, Hostname, MAC, Category, IP, Network and `[   UP   ]`-style status), `device; status` (`name ..... [   UP   ]`),
`category; list`, `jobqueue; list` (from `sinfo`) and `monitoring; healthchecks; list`. Verbs that change state are refused
(`Error: this cmsh is read-only`, exit 1); the interactive console accepts the same read-only subset. The head node is the login container itself.

## Addendum: the events log (issue 714)
The events-log tail is no longer out of scope. bcm-mock records every node state change (a scenario, the web page, a power call or `/sim/inject`; a repeat of the same state is not an event) and serves them as `cmevents.getEvents` (`args: [since_seq]`, answer `{seq, events: [{seq, time, severity, host, text}]}`, at most 1000 kept). `images/login/events_log.py` runs on the head node (the login container, or the controller when there is one) and appends each event to `/var/spool/cmd/events.log` (mode 644, rewritten when the process starts, polled every 3 s; a bcm-mock restart makes the counter go back and the feed start over). A line looks like

    Tue Oct  6 12:00:00 2026 [alert] cn001: Device status changed from UP to DOWN

with ctime-style date (day of month padded with a space), `[severity]` (`alert` for DOWN, `notice` for UP, `warning` for CLOSED or INSTALLING) and `host: text`. **Not confirmed against a real CMDaemon:** NVIDIA's Base Command Manager manuals name the `events` command and the `/var/spool/cmd` directory but do not document the file or its line format, so this is the documented shape of CMDaemon's messages as the one client we know reads them, not a capture. Replace it with a fixture when a real file is available (NF-2). Only state changes are logged; CMDaemon's many other event kinds are not simulated.
