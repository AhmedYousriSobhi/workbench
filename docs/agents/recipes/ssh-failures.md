# Recipe: ssh-failures

## Goal
Check the app tells "not authorised" from "unreachable" and from "one node's sshd is down".

## Needs
Profile `min`. Scenarios `ssh-key-refused` (the login node stops accepting the key) and `ssh-node-sshd-down` (sshd on `cn001` stops).

## Steps
```bash
./scripts/benchctl start
./scripts/benchctl wait ready --timeout 180
./scripts/benchctl ssh-key install
./scripts/benchctl scenario run ssh-key-refused --json
./scripts/benchctl scenario run ssh-node-sshd-down --json
```
Each scenario restores itself after 60 s.

## What your app should show
For `ssh-key-refused`: "Permission denied (publickey)" reported as not authorised, not as unreachable. For `ssh-node-sshd-down`: the hop to `cn001` fails while `cn002` still works.

## Assert
Both scenario results have `"passed": true`. Judge the app's message by hand: workbench cannot read it.

## Clean up
```bash
./scripts/benchctl stop
```

## Memory cost
`min`: 0.5 GB expected, 1.5 GB with the app reserve (`docs/guides/memory.md`).
