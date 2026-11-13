# Recipe: connect-your-app

## Goal
Point the app under test at workbench and prove it can reach every service it needs.

## Needs
Profile `min` (4 nodes, the mocks and scenarios). Add `--with jira` only if the app talks to a real Jira.

## Steps
```bash
./scripts/benchctl start
./scripts/benchctl wait ready --timeout 180
./scripts/benchctl describe workbench --json
./scripts/benchctl export profile -o bench-profile.json
./scripts/benchctl ssh-key install
./scripts/benchctl get endpoints --json
```
`describe workbench --json` lists each endpoint with the file that holds its credential (a path, never the secret). `export profile` writes the file the monitoring client imports; `ssh-key install` prints the key path to give it.

## What your app should show
Every component it was configured for connects: SSH login, Slurm, BMC (Redfish), BCM, Jira, Grafana. Follow `docs/guides/client-setup-by-component.md` one component at a time.

## Assert
`wait ready` exits 0, and `get traffic --json` shows requests from your app under `streams` once it connects.

## Clean up
```bash
./scripts/benchctl stop
```

## Memory cost
`min`: 0.5 GB expected, 1.5 GB with the 1 GB reserve for the app (`docs/guides/memory.md`). `benchctl plan --json` gives the exact figure for your machine.
