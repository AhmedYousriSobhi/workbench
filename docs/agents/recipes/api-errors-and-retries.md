# Recipe: api-errors-and-retries

## Goal
The Jira API rate-limits or goes down; check the app backs off and retries.

## Needs
Profile `min` (Jira mock). Scenarios `jira-429` (429 with Retry-After for 5 calls) and `jira-outage` (503 for 20 calls, cleared at 60 s).

## Steps
```bash
./scripts/benchctl start
./scripts/benchctl wait ready --timeout 180
./scripts/benchctl scenario run jira-429 --json
./scripts/benchctl get traffic --json
./scripts/benchctl scenario run jira-outage --json
./scripts/benchctl get faults --json
```
`get faults --json` lists the Jira failure mode while it has requests left to fail.

## What your app should show
Backoff that honours `Retry-After`, no tight retry loop, and a clear "Jira unavailable" state during the outage that clears after it.

## Assert
In `get traffic --json`, `streams` for `jira-mock` show your app's request count and its `client_error` / `server_error` counts; they should rise by about the failed calls, not by hundreds.

## Clean up
```bash
./scripts/benchctl stop
```

## Memory cost
`min`: 0.5 GB expected, 1.5 GB with the app reserve (`docs/guides/memory.md`).
