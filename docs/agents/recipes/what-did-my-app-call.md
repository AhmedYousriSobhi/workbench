# Recipe: what-did-my-app-call

## Goal
See which workbench services your app called, how often, and how each call ended.

## Needs
Profile `min`, and your app already pointed at workbench (see `connect-your-app`).

## Steps
```bash
./scripts/benchctl start
./scripts/benchctl wait ready --timeout 180
./scripts/benchctl get traffic --json
./scripts/benchctl get traffic --since 1790000000 --json
```
`--since` takes seconds since the epoch and keeps only newer entries in `recent`; `streams` is always cumulative. The record covers the three mocks (BMC farm, BCM, Jira) and keeps the caller's User-Agent product name only, never a body.

## What your app should show
Nothing: this is a read-only view of what it did.

## Assert
`services` has `true` for each mock that answered; `false` means it is not reachable, `null` means an older image with no record. Each `streams` row gives `who`, `shape`, `n`, `ok`, `client_error`, `server_error`.

## Clean up
```bash
./scripts/benchctl stop
```

## Memory cost
`min`: 0.5 GB expected, 1.5 GB with the app reserve (`docs/guides/memory.md`).
