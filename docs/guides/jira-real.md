# Real Jira (optional)

`jira-mock` is the default and answers on `http://127.0.0.1:8080`. When you need Atlassian's own code (to see how a
client behaves against real validation, JQL and workflows), start the **real Jira Data Center** next to it. It is a
session tool: the test licence lasts **3 hours**, then the server is read-only and you reset it. About 3 GB of memory
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

## When the 3 hours are up

Jira turns read-only. Start from nothing; the same key gives a new 3 hours, counted from the setup (as in the lab this part was ported from):

```bash
./scripts/benchctl remove jira-real-state      # removes the containers and data; the mock is untouched
./scripts/benchctl start --with jira-real && ./scripts/benchctl setup jira-real
```

## Memory

`benchctl plan --with jira-real` shows the estimate before anything starts (the part adds 3.3 GB: Jira 3 GB, Postgres
0.25 GB). The governor treats it as class P6, the first thing it stops under pressure. `--addons` and `all` never turn it
on: only its name does.

## What is real and what is not

Real: Atlassian's code, REST v2, workflow, JQL, custom fields, validation. Not real: it is not Cloud (ADF comment bodies,
`accountId`), it is one node, and the licence is a test key. Cloud shapes are checked by the recorded contract tests
(Jira contract tests (issue 229)).

## Test it

```bash
./scripts/verify.sh --unit jira_dc            # the setup flow against a fake Jira (no Docker), then the live round trip
./scripts/verify.sh --unit jira_dc --clean    # first remove the real Jira's data
```

The live part skips and says where to put the licence when it is missing; with it, it needs about 3.3 GB free and a few minutes.

## Ticket automation on both Jiras

The same automation runs on the mock and on this Jira, from the host (the real Jira has no private `/sim/*` endpoints):

```bash
./scripts/benchctl run ticket-team --target real --every 20
./scripts/benchctl run ticket-watch --target real
```

Details and the differences between the two workflows: [ticket driver](jira-tickets.md#both-jiras-one-script-issue-284).
