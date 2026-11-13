# Recipe: node-down

## Goal
A compute node loses power; check the app shows it down and recovers.

## Needs
Profile `min`. The scenario `node-down` uses `cn002`.

## Steps
```bash
./scripts/benchctl start
./scripts/benchctl wait ready --timeout 180
./scripts/benchctl scenario run node-down --json
./scripts/benchctl get faults --json
```
`scenario run` plays the 40 s timeline: BMC power off, BCM state DOWN, Slurm drain, then everything restored. Run `get faults --json` while it plays (from a second shell) to see `cn002` listed.

## What your app should show
`cn002` down in the BMC and BCM views and drained in Slurm, then back to normal at 40 s.

## Assert
The scenario result has `"passed": true` and the `expect` entry for `slurm.node_state` is `ok`; `get faults --json` has no `cn002` row afterwards.

## Clean up
```bash
./scripts/benchctl stop
```

## Memory cost
`min`: 0.5 GB expected, 1.5 GB with the app reserve (`docs/guides/memory.md`).
