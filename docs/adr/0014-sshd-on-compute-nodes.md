# 0014. sshd on the compute nodes

Status: accepted

## Context
Users and monitoring clients reach a compute node from the login node: `ssh cn001`, a client's execution target
(it runs `ssh <node> <command>` from the connected node), a ProxyJump or forwarded channel to `cn001:22`, and
`srun --pty`. ADR 0011 gave only the login node an `sshd`.

## Decision
- `node-base` carries `openssh-server` and the Slurm tools linked into `/usr/local/bin`. Its entrypoint starts `sshd`
  on a node only when `HPCSIM_SSHD=1` and the keys are mounted (compose sets both for every compute and GPU node), so
  the controller and other users of the image run none.
- Same rules as the login node: key login only, no root, no password, `UsePAM no`, only the simulated users;
  TCP forwarding is off on nodes. No published port: nodes are reached through the login node only.
- `benchctl up` makes two more keys in `compose/generated/secrets/ssh/` (kept across runs): one host key shared by all
  nodes, and a cluster key. Each login-node user gets the cluster key as `~/.ssh/id_ed25519`, as a shared home
  directory would give on a real cluster; `authorized_keys` lists the client key and the cluster key.
- The login node trusts the shared node host key for `cn*` and `gpu*` in `/etc/ssh/ssh_known_hosts`, so the hop
  has no prompt. One key for all nodes is a simulation shortcut, like the shared munge key.
- sshd on a node uses about 4 MB (a node is limited to 60 MB).

## Consequences
- `ssh cn001` from the login node, a client ProxyJump to a node, and `srun` / `srun --pty` all work.
- `up` now builds `node-base` first and `slurmctld`/`slurmrestd` get their `images/slurm-ctl` build context.
  Before, a change to the base image (a new munge key) left the controller on the old image and every Slurm call
  failed with "Protocol authentication error".
- Cert-based access (Teleport, issue 39) and the fake `cmsh` (issue 66) still build on the login node.
