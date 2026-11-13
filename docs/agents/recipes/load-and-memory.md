# Recipe: load-and-memory

## Goal
Run the app against a busy cluster and know what the memory governor is doing.

## Needs
Profile `min` or `std`. The governor protects the machine: a `REFUSED` start is a decision, not a fault.

## Steps
```bash
./scripts/benchctl plan --json
./scripts/benchctl start
./scripts/benchctl wait ready --timeout 180
./scripts/benchctl load on --rate 6 --duration 120
./scripts/benchctl status --json
./scripts/benchctl load off
```
`plan --json` says whether it fits before anything starts. `load on` starts the user simulator (alice and bob submit jobs); `load off` stops it.

## What your app should show
A live queue and changing node states.

## Assert
`status --json` has `governor.level` 0 while it runs. A higher level means memory is short: the governor pauses, then stops, low-priority containers (`docs/manual/07-governor.md`).

## Clean up
```bash
./scripts/benchctl stop
```

## Memory cost
`min`: 0.5 GB expected; `std`: 1.3 GB; each with the 1 GB app reserve (`docs/guides/memory.md`).
