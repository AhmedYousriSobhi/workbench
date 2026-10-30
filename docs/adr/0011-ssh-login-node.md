# 0011. An SSH login node for SSH-based clients

Status: accepted

## Context
HPC monitoring clients do not call `slurmrestd`. They open one SSH connection to a login node and run
`squeue`, `sinfo` and `sacct` there; Kubernetes, GPU and storage reads, BMC channels (`forwardOut`) and the BCM
head-node hop ride the same connection. Simulated nodes had no `sshd`, so none of that could reach the cluster.

## Decision
A `login` service (group `access`, in `min` and so every profile) built from `images/login` on `node-base`:
- `sshd` with key login only, no root, no password, `UsePAM no`, TCP forwarding on (BMC and other channels).
  Only the simulated users from `sim.yaml` may log in; their accounts are unlocked for keys (`*`, still no password).
- Slurm client tools are linked into `/usr/local/bin`, because sshd gives non-interactive sessions a minimal PATH.
- Published on `127.0.0.1:2222` only. Unprivileged (default capabilities, no `cap_add`), 40 MB limit, class P2.
- `benchctl up` creates `compose/generated/secrets/ssh/` once (gitignored, 0600): a host key (so a client that pinned it
  keeps trusting it across runs), a client key `id_ed25519` and `authorized_keys`. Mounted read-only; the container
  copies the host key to a root-owned path because sshd refuses a key owned by anyone else.
- No sudo anywhere on the path (NF-5).

## Consequences
- `ssh -i compose/generated/secrets/ssh/id_ed25519 -p 2222 alice@127.0.0.1 sinfo` works after `benchctl up`.
- Compute nodes still have no `sshd`; Slurm's `srun` and a client's `ssh <node>` hops are not covered (follow-up).
- Teleport (issue 39) can build on this component.
