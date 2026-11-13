<p align="center"><img src="../assets/art/guide-jira-real-hero.svg" alt="Two Jira chips: the mock on port 8080 and the real one on port 8083" width="720"></p>

# Real Jira (optional)

**In 30 seconds:** the Jira mock is the default. When you need Atlassian's own code, start the real Jira Data Center next to it: about 2.4 GB, and a test licence that lasts 3 hours per start.

## Do it

```bash
./scripts/benchctl start --with jira-real
./scripts/benchctl setup jira-real
```

`jira-mock` is the default and answers on `http://127.0.0.1:8080`. When you need Atlassian's own code (to see how a
client behaves against real validation, JQL and workflows), start the **real Jira Data Center** next to it. It is a
session tool: the test licence lasts **3 hours** from each start of Jira, then the server is read-only; `benchctl refresh jira-real-licence` (or the button) renews it. About 2.4 GB of memory (2.1 GB for Jira, the smallest limit that worked, plus Postgres)
(ADR 0026, SPEC C-19). It is Data Center, not Cloud.

## Once: get the test licence

Atlassian publishes test keys for developers at
<https://developer.atlassian.com/platform/marketplace/timebomb-licenses-for-testing-server-apps/>. Use
**"10 user Jira Software Data Center license, expires in 3 hours"** (not an app licence). Save it as one line:

```bash
mkdir -p compose/generated/secrets && printf '%s\n' 'PASTE-THE-KEY-HERE' > compose/generated/secrets/jira.license
```

The file is gitignored; never commit it. `WORKBENCH_JIRA_LICENSE_FILE=/path` or `benchctl setup jira-real --licence FILE`
read it from elsewhere.

## Start

```bash
./scripts/benchctl start --with jira-real      # adds the part; with other parts it keeps them
./scripts/benchctl setup jira-real             # the setup wizard, users, project OPS, tickets, tokens (a few minutes)
```

The first start pulls about 1 GB. `setup` prints one line per step and ends with
`Jira (real): http://127.0.0.1:8083`. It is safe to repeat, and it only talks to localhost.

- Users: `admin`, `alice`, `bob`, `carol`, `dave`, `erin` (the names the mock's team uses). Passwords and one personal
  access token each for admin and alice are in `compose/generated/secrets/jira-real.env` (the Connections page shows the
  path, never the content).
- Project `OPS`, 24 tickets. Its workflow is Jira's own (To Do, In Progress, Done), not the mock's (Open, In Progress,
  Resolved, Closed): tools must read the transitions Jira offers, not assume names.
- The page's Connections tab has an **Open Jira (real)** link.

### Why 2.1 GB (measured, issues 287 and 289)

A JVM holds more than its heap: metaspace, a code cache, thread stacks, compiler threads and the plugin system. Measured on the live
stack with the same procedure each time (setup wizard, seeding, 20 ticket changes, no Docker restart allowed):

| Heap | JVM flags | Limit | Result | Peak |
|---|---|---|---|---|
| 768 MB | default | 1536 MB | failed, killed 4 times | 1513 MiB |
| 768 MB | default | 2048 MB | failed, setup lost the connection | 2024 MiB |
| 768 MB | default | 2304 MB | finished, killed once | 2302 MiB |
| 768 MB | default | 2560 MB | passed | 2363 MiB |
| 768 MB | `JVM_ARGS` below, metaspace capped at 192 MB | 2304 MB | failed: `OutOfMemoryError: Metaspace` | |
| 768 MB | `JVM_ARGS` | 2304 MB | passed | 2043 MiB |
| 768 MB | `JVM_ARGS` | **2176 MB** | **passed twice** | **2039 and 2045 MiB** |

`JVM_ARGS` is `-XX:+UseSerialGC -XX:TieredStopAtLevel=1 -Xss512k -XX:CICompilerCount=1` (one collector thread, C1-only compilation,
smaller thread stacks, one compiler thread): it takes about 320 MiB off the peak. Capping metaspace does not work: Jira's 273
plugins need it. The peak stays near 2040 MiB whatever the limit, so the limit is that peak plus about 6 percent. The constants
are `HEAP_MIN_MB`, `HEAP_MAX_MB`, `CODE_CACHE_MB`, `LIMIT_MB`, `JVM_ARGS` in `benchctl/jira_real.py`; change one only after repeating
the trial and the numbers in [memory](memory.md). The cost of C1-only compilation is a slower Jira under heavy load, which a
simulation does not generate.

### Switching plugins off does not help (measured, issue 289)

The image ships 273 plugins, 239 of them bundled. I switched groups off through the plugin manager API, one group at a time,
restarted Jira and measured: all plugins on, 1.80 to 1.92 GiB after the restart; 44 language packs off, 1.80 GiB; 18 extras
(onboarding, credits, help tips, feedback, quick search, ...) off, 1.86 GiB. Analytics and the integrations (DVCS, streams,
importers, automation, mobile) cannot go: with either group off, Jira's own REST, project and issue-navigator plugins did not
start (`/status` says `ERROR`; the way back is `delete from pluginstate` in the Postgres container). So there is no plugin set that
saves memory, and the part does not offer one.

## On the page

With the part built, the Infrastructure architecture draws a **Jira DC** box next to **Jira** (the simulated one) under *Services: what
clients call*. The word "mock" is not in the boxes: **colour says it**, and the colour legend under the diagram explains it.

- **What it is:** violet with a dashed stripe = simulated; blue with a solid stripe = real. (Hover or click for the words.)
- **State (the border):** green = up, red dashed = not reachable, grey dotted = off.
- **On top, and they combine:** a pulsing blue dot = traffic now (for a node: GPU work running); a red ring with a red triangle = a
  fault applied in the last 30 seconds. One box can be up, busy and faulted at the same time.

The real Jira has **no request counter** (only the simulated parts count requests), so its box shows up or down and says
`no counter`; its dot never pulses. Click a box for its address, the command to test it and the licence line (Connections).
Without the part the box is not drawn.

## When the 3 hours are up

The 3 hours count from the **start of Jira**, not from the setup: measured on this part, the licence end moved from 14:30 to
15:35 (UTC) when the container was restarted at 12:35. So a restart renews it, and your users, tickets and tokens stay:

```bash
./scripts/benchctl refresh jira-real-licence   # restarts Jira, waits until it answers, prints the new end time
```

The same is a button: **☰ Menu → Connections**, row *Jira (real, Data Center)*, **Refresh licence (restarts Jira)**. It asks the
governor (the page has no Docker access) and needs calm memory (L0-L1); the progress shows in *Governor activity*. Jira is
unreachable for about a minute while it starts. Do it before the 3 hours end (Jira warns when under 24 hours are left) or after: it
works either way.

**Automatic.** While the web page runs (`benchctl start`), it reads the licence end once a minute (over HTTP, no Docker) and shows it
in the Connections row (`licence: valid until 17:42 (in 2 h 55 min)`); `benchctl status` prints the same line. When less than
**15 minutes** are left, or the licence has already ended, it asks the governor for the same refresh, at most once per 15 minutes,
and the row says `licence: renewing now`. Change the threshold with `WORKBENCH_JIRA_AUTO_REFRESH=MINUTES` (0 turns it off) in the
environment of `benchctl start`. It needs the governor at calm levels (L0-L1); otherwise it waits and asks again. Jira restarts
once about every 2 h 45 min, so a client talking to it sees about a minute of downtime then; turn it off if that matters.

If the refresh says the licence is still not valid, the key file itself has a fixed end date: save a fresh published key in
`compose/generated/secrets/jira.license`, then start from nothing (this deletes users, tickets and tokens; the mock is untouched):

```bash
./scripts/benchctl remove jira-real-state
./scripts/benchctl start --with jira-real && ./scripts/benchctl setup jira-real
```

## Memory

`benchctl plan --with jira-real` shows the estimate before anything starts (the part is expected to use about 2.3 GB: Jira at its 2.1 GB cap, Postgres about 0.15 GB; both are capped at 2.4 GB). The governor treats it as class P6, the first thing it stops under pressure. `--addons` and `all` never turn it
on: only its name does.

## What is real and what is not

Real: Atlassian's code, REST v2, workflow, JQL, custom fields, validation. Not real: it is not Cloud (ADF comment bodies,
`accountId`), it is one node, and the licence is a test key. Cloud shapes are checked by the recorded contract tests
(Jira contract tests (issue 229)).

## Faults on the real Jira (scenarios)
The scenario target `jira-real` makes the real Jira fail in the ways integrations meet (Atlassian's rate-limit page and the community's usual failures), with real behaviour; slow, 503 and 429-with-Retry-After need the small fault proxy (below). `fault:` is one of:

| fault | what happens | what a client sees |
|---|---|---|
| `outage` / `restore` | `docker pause` / `unpause` of the Jira container | calls hang, then time out; then work again |
| `bad_logins` (`user`, `count`) | wrong-password Basic logins | 401, then 403 with `X-Authentication-Denied-Reason: CAPTCHA_CHALLENGE` (REST cannot solve a CAPTCHA; a personal access token is not affected) |
| `clear_logins` | zeroes every user's failed-login count in Jira's own table (what an admin's "reset failed login count" does) | the user can log in again |
| `token_revoke` / `token_restore` (`user`, default `alice`) | deletes the user's `workbench` personal access token in Jira; restore makes a new one and rewrites `PAT_<USER>` in `compose/generated/secrets/jira-real.env` (0600, never printed) | a client using the old token gets a real 401 until it is given the new value; the ticket driver uses admin's token and is not affected |
| `proxy_slow` (`count`, `delay`) / `proxy_503` / `proxy_429` (`count`, `retry_after`) / `proxy_off` | sets the fault proxy (below) | the next `count` REST calls through the proxy are delayed `delay` s then answered, or get 503 / 429 with `Retry-After: retry_after` (default 30); then normal |
| `rate_limit` (`capacity`, `fill_rate`) / `rate_limit_off` | turns Jira's own rate limiting (Administration, Rate limiting) on or off in its tables and bumps the settings version | 429 with `X-RateLimit-Limit/Remaining/FillRate/Interval-Seconds` and `Retry-After`; Jira reloads about once a minute, so the change shows 30 to 60 s later (measured: about 40 s); `myself`, `serverInfo` and `search` are not counted, `project`, `field` and `issue` are |

Run `benchctl scenario run jira-real-outage`, `jira-real-429`, `jira-real-captcha`, `jira-real-token-revoked` or `jira-real-slow-503` (needs `jira-real` up). A client should send authenticated requests (Basic or session): rate limiting counts per user.

**Fault proxy (issue 305).** Jira has no setting for a slow answer or a 503, so a scenario with a `proxy_*` step starts a small standard-library proxy on the host, `127.0.0.1:8084`, in front of Jira (`benchctl/jira_proxy.py`, a few MB, up when the run starts and gone when it ends). **Point the client at `http://127.0.0.1:8084`** for those scenarios (`:8083` never meets the proxy faults). Only `/rest/` calls are failed; `/status` and everything else pass through, and after `count` calls it passes everything again. If port 8084 is taken the run says so and the `proxy_*` steps fail.

**Tickets in scenarios (issue 306).** A `jira-real` step can carry `tickets: {action: create|transition|comment, ...}` (same body as the mock's, `create` takes `count`, `summary` with `{n}`, `labels`, `priority`; `transition` and `comment` take a `jql`), done through the standard REST API with admin's token. The expectation `jira.issue_count` / `jira.issue_status` takes `target: real` to ask the real Jira. `client-stress-real` (SC-02) uses both for its 12-ticket wave.

## Test it

```bash
./scripts/verify.sh --unit jira_dc            # the setup flow against a fake Jira (no Docker), then the live round trip
./scripts/verify.sh --unit jira_dc --clean    # first remove the real Jira's data
```

The live part skips and says where to put the licence when it is missing; with it, it needs about 2.4 GB free and a few minutes.

## Ticket automation on both Jiras

The same automation runs on the mock and on this Jira, from the host (the real Jira has no private `/sim/*` endpoints):

```bash
./scripts/benchctl run ticket-team --target real --every 20
./scripts/benchctl run ticket-watch --target real
```

Details and the differences between the two workflows: [ticket driver](jira-tickets.md#both-jiras-one-script-issue-284).

## Under the hood

<p align="center"><img src="../assets/art/guide-jira-real-diagram.svg" alt="The mock answers on 8080; the real Jira Data Center and its Postgres answer on 8083" width="720"></p>

> [!HOOD]
> `jira-real` is an optional part, off by default: the official `atlassian/jira-software` image (pinned) with `postgres:15` on collation C, on 127.0.0.1:8083. `--addons` and `all` do not turn it on; only its name does.
> Jira gets 2176 MB (a trimmed JVM) and Postgres 256 MB, both class P6, the smallest limits that passed setup and a ticket run twice. The mock keeps 127.0.0.1:8080.

Source: `benchctl/jira_real.py`, [ADR 0026](../adr/0026-real-jira-data-center-as-an-optional-part.md), SPEC C-19

## Tips

> [!WARNING]
> The test licence is a gitignored file. Never commit it.

## If it goes wrong

| Symptom | Try |
|---|---|
| Jira turns read-only | the 3 hours are up: `benchctl refresh jira-real-licence` |
| `up` is refused | the real Jira needs about 2.4 GB; free memory or leave it off |
| Start over | `benchctl remove jira-real-state` |

## Learn more

- [Jira tickets](jira-tickets.md)
- [Jira contract](jira-contract.md)
- [How much memory it uses](memory.md)
