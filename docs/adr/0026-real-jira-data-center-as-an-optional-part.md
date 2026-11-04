# ADR 0026: A real Jira Data Center as an optional part, next to the mock

Status: accepted (issue 284; builds on ADR 0005 and ADR 0025)

## Context
`jira-mock` is our own stdlib program (ADR 0005). It is small, instant to reset and can inject failures, but nothing in it is
Atlassian's code, and until the contract tests (issue 229) nothing compared it with real Jira. ADR 0025 left real Jira as "too
heavy to run by default". A working real-Jira lab already existed outside this repository: it showed that the official image,
Postgres and a scripted setup wizard run on a developer machine.

## Decision
Add `jira-real`, an **optional part, off by default**, that runs the official `atlassian/jira-software` image (pinned,
10.3.9, the version the setup was written against) with `postgres:15`. The mock stays the default and keeps its address
(127.0.0.1:8080); the real one is on 127.0.0.1:8083. `--addons` and `all` do **not** turn it on; only its name does
(`--with jira-real`, or `ticketing jira-real`).

| Choice | Why |
|---|---|
| Postgres, collation C | Jira 10's wizard has no built-in database; Jira refuses any other collation. |
| 3072 MB for Jira (heap 1 to 2 GB), 256 MB for Postgres, class P6 | About 3 GB measured by the lab; P6 so the governor stops it first. Counted in the estimate and in governor admission like every part. |
| Named volumes, removed by `benchctl remove jira-real-state` | The data survives `down`; a fresh start is a reset, not a rebuild of the image. |
| Setup by code (`benchctl setup jira-real`, `benchctl/jira_real.py`) | The wizard (database, properties, licence, administrator, no mail) is five form posts; then it waits until REST really answers (`/status` says RUNNING first), seeds users alice to erin (the mock team's names), project `OPS` (the key the mock and the exported profile use) and tickets, and makes a personal access token per user. Safe to repeat. |
| Localhost only | The setup refuses any base URL that is not 127.0.0.1, localhost or ::1, so a typo cannot seed a real site. |
| Generated passwords | Database, administrator and user passwords are random, made once in `compose/generated/secrets/` (gitignored), never printed. |
| The licence is a file, never committed | Atlassian's published 3-hour Data Center test key (<https://developer.atlassian.com/platform/marketplace/timebomb-licenses-for-testing-server-apps/>, "10 user Jira Software Data Center license, expires in 3 hours"), saved as `compose/generated/secrets/jira.license` or named by `$WORKBENCH_JIRA_LICENSE_FILE`. After 3 hours the server is read-only: the part is a session tool, started, used and reset. |

## What is real and what is not
Real: Atlassian's code, Data Center REST (v2), workflow, JQL, custom fields and real validation. **Not real**: it is Data Center,
not Cloud (Cloud's ADF comment bodies, `accountId`, `search/jql` paging stay checked only by the recorded Cloud shapes, issue
229); one node, no clustering; the licence is a test key; no webhooks are wired to the stack.

## Consequences
- Memory: the `jira-real` profile and the part add about 3.3 GB. With both Jiras on, the min preset no longer fits a 16 GB host
  comfortably; the estimate says so before `up`.
- Everything that talks to Jira must work against both: the ticket driver (issue 262) is tested against the mock and the real one.
- Atlassian ended new Data Center sales on 30 March 2026 and ends Data Center in 2029; the test key and the image still
  work today. If either disappears the part is dropped and this ADR superseded.
- Not exercised here: Jira Cloud, a licensed Jira, clustering, the Marketplace.
