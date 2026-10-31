# Tickets: create, note, edit, change status, and scenarios for them

The Jira mock behaves like a small Jira, so a monitoring client can do everything it does with tickets, and a scenario can
file and move tickets next to a real fault (a drained node, a GPU error). Everything is at `http://127.0.0.1:8080`;
set up the connection first in [client setup, section 5](client-setup-by-component.md).

## What a client can do against it

| Client action | Call (REST v2, `/rest/api/3/...` works too) |
|---|---|
| Search and list | `GET|POST /search`, `/search/jql` (JQL: `labels = "workbench"`, `text ~ "cn003"`, `statusCategory != Done`) |
| Open a ticket | `GET /issue/KEY` (fields, comments, `watches`) |
| Create a ticket | `POST /issue` `{"fields": {"project": {"key": "OPS"}, "summary": "..."}}` (summary and project are required) |
| Change status | `GET /issue/KEY/transitions`, then `POST /issue/KEY/transitions` (Resolve needs a `resolution`) |
| Notes | `POST|GET /issue/KEY/comment`, `PUT|DELETE /issue/KEY/comment/ID` (`visibility` marks an internal note) |
| Edit | `PUT /issue/KEY` (summary, description, priority, labels), `PUT /issue/KEY/assignee` |
| Watch, log work | `GET|POST|DELETE /issue/KEY/watchers`, `POST|GET /issue/KEY/worklog` (`1h 30m`) |
| Pick a person | `GET /user/search`, `/user/assignable/search` (`alice`, `bob` and anyone seen on a ticket) |

The workflow is **Open, In Progress, Resolved, Closed**; Reopen from Resolved or Closed. Authorization headers are accepted
and never checked (a Bearer token or a Basic `email:token` names the user).

## Tickets that name the cluster and the node

The client scopes tickets by a label (its guide's recipe: `project = OPS AND labels = "<cluster>"`) and finds a node's tickets
with `text ~ "cn003"`. The shipped scenarios label every ticket with the cluster name `workbench` (the name `benchctl export
profile` gives the cluster) and the node, and put the node in the summary. Name your client's cluster `workbench`, or change
the label in your own scenario.

## Scenarios

```bash
./scripts/benchctl scenario run drain-ticket          # add --speed 10 to play it ten times faster
```

| Scenario | What happens |
|---|---|
| `ticket-lifecycle` | one ticket goes Open, In Progress, gets a note, Resolved, Closed (about a minute) |
| `drain-ticket` | Slurm drains `cn003`, a High ticket is filed for it, moves to In Progress, gets a note, the node resumes and the ticket is Resolved |
| `ticket-wave` | twelve tickets at once: all four statuses, three priorities, spread over `cn002`, `gpu001`, `cn004` and the cluster |
| `jira-flap` | Jira is refused (401), rate limited (429), unavailable (503), each time then healthy again, with a ticket before and after |
| `jira-outage`, `jira-429` | Jira unavailable or rate limited for a while |

**Switching Jira on and off** is the `failure` step: `{failure: {mode: "503", count: 20}}` switches it off for 20 calls (or
`401`, `429`, `slow`), `{failure: {mode: null}}` switches it on again.

## Your own ticket steps

A `jira` step with `tickets:` files or moves tickets. `action` is one of:

| action | needs | does |
|---|---|---|
| `create` | `summary` | files `count` tickets (default 1; `{n}` in `summary` is the number); optional `project` (OPS), `labels`, `priority`, `description`, `assignee`, `issuetype`, and `status` to leave them in (the workflow is walked as a person would) |
| `transition` | `jql`, `to` | moves every ticket the query finds to Open, In Progress, Resolved or Closed (`resolution` defaults to `Done`) |
| `comment` | `jql`, `body` | adds a note to every ticket the query finds |
| `reset` | | deletes every ticket and restarts the numbering at 1 |

```yaml
steps:
  - {at: 0s,  target: slurm, do: {drain: [cn002], reason: "disk"}}
  - {at: 5s,  target: jira,  do: {tickets: {action: create, summary: "cn002 drained", labels: [workbench, cn002], priority: High}}}
  - {at: 40s, target: slurm, do: {resume: [cn002]}}
  - {at: 45s, target: jira,  do: {tickets: {action: transition, jql: 'labels = cn002 AND statusCategory != Done', to: Resolved}}}
expect:
  - jira.issue_status: {jql: 'labels = cn002', status: Resolved, within: 60s}   # every match is Resolved
  - jira.issue_count:  {jql: 'labels = wave', count: 12, within: 10s}
```

`benchctl scenario run NAME --dry-run` checks the step fields before anything runs. Without a client at all, the same
control is `POST /sim/tickets` (and `GET /sim/tickets` for the count per status), which is not counted as client traffic:

```bash
curl -s -X POST 127.0.0.1:8080/sim/tickets -d '{"action":"create","count":3,"summary":"Job {n} failed","labels":["workbench"]}'
curl -s 127.0.0.1:8080/sim/tickets
curl -s -X POST 127.0.0.1:8080/sim/tickets -d '{"action":"reset"}'
```

## What the client shows

Tested against the real desktop client with `drain-ticket`: give the client the cluster's Jira token (any text; the mock
accepts it), then run the scenario. The ticket reaches the client's Jira cache with its key, title and priority, moves to
In Progress and then Resolved, and the client raises its own notification (`OPS-1 on workbench moved to "Resolved"`).
The client reads Jira on its own timer and a manual refresh waits at least 30 s between reads, so a change shows up
within about a minute, not at once.

The client has no "create ticket" call for Slurm; what it does on a ticket works against the mock: read details and
transitions, comment, assign, watch, log work, edit priority, labels and description (the mock serves `editmeta`), and move
it with the Resolve form (the mock describes the resolution choices). Create the ticket with a scenario or `POST /sim/tickets`.

## What is not simulated

Jira Cloud's Teams and Assets fields, attachments, linked tickets, Confluence, and transition screens with extra fields. A
client that needs one of them reads an empty answer, not an error. The mock keeps tickets in memory: a restart empties it.


## Tickets for node faults

Every node fault files a ticket and every recovery resolves it, like an ops integration would. It happens in the same place as the fault, so the page's buttons, `benchctl set node NAME off|on` and a scenario's `bmc-farm` and `gpu-exporter` steps all do it. With no Jira in the cluster nothing is filed and nothing fails.

| Fault | Ticket summary | Priority |
|---|---|---|
| Power off | `cn002: node is down (powered off)` | Highest |
| GPU error (XID) | `gpu001: GPU error` | High |
| Overheat (92 °C) | `cn002: node is overheating` | High |
| Fan failure | `cn002: fan failure` | Medium |

One ticket stays open per node and kind: the same fault again adds a comment (`Still failing.`) instead of a second ticket. Power on or Clear faults adds `Recovered.` and moves the ticket to Resolved; a fault after that opens a new one. Tickets have the labels `node-<name>`, `fault-<kind>` and `auto`, type Incident, reporter `ops-bot`.

`POST /sim/tickets {"action": "fault", "node": "cn002", "kind": "power", "detail": "..."}` does the same by hand (`"open": false` resolves; leave `kind` out to resolve every kind for the node).
