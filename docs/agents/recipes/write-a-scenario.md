# Recipe: write-a-scenario

## Goal
Add a named, repeatable fault timeline of your own.

## Needs
Any running profile. The format is in `docs/manual/06-scenarios.md`; targets include `bmc-farm`, `bcm-mock`, `gpu-exporter`, `jira`, `slurm`, `ssh`, `k8s`.

## Steps
```bash
./scripts/benchctl scenario list --json
cp scenarios/node-down.yaml scenarios/my-fault.yaml
./scripts/benchctl scenario run my-fault --dry-run --json
./scripts/benchctl scenario run my-fault --json
```
Edit `name`, `description` and the `steps` (`{at: 5s, target: slurm, do: {drain: [cn001], reason: "test"}}`) before the dry run. Give it a new `ref` (the next SC number).

## What your app should show
Whatever your steps cause. `--dry-run` validates the file and prints the timeline without touching the cluster.

## Assert
The dry run exits 0 with `"dry_run": true`; the real run has `"passed": true`, including every `expect:` you added.

## Clean up
Delete the file, or keep it: it is a normal scenario in the repository.

## Memory cost
None beyond the running profile.
