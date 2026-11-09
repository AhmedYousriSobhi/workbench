# ADR 0036: Controller node and jump host

Status: accepted (issues 415, 416). Decided by the owner.

## Context
Real sites separate login nodes (users), a controller (where slurmctld runs and admins work, usually also the BCM head node) and
often a bastion in front of them. Until now the login node was the BCM head node and the "jump host" in the diagram, and
`slurmctld` was a container with no way in.

## Decision
- **Controller** (`sim.yaml` `controller.enabled`, `--controller`, the Cluster architecture sheet): the existing `slurmctld`
  container also runs sshd (alias `controller`, 10.141.255.1, fallback 127.0.0.1:2221). Only whitelisted users may log in: those in
  `controller.allow.users` plus every user whose `groups` meet `controller.allow.groups` (default group `admins`). `cmsh` is
  bind-mounted there and removed from the login nodes (`WORKBENCH_HEAD=0`), which keep the Slurm tools and kubectl. With no controller the
  login node acts as one, as before. It is the same process container, not a new one, so slurmctld really runs on the controller.
- **Jump host** (part `jump`, `--access jump`): a new image, `images/jump`, with sshd and nothing else (Ubuntu base plus
  openssh-server), 10.141.255.5, fallback 127.0.0.1:2220, TCP forwarding on, the same users and keys, its own host key. It fronts the
  key-login nodes (s-login, k-login) and the controller with `ssh -J`; Teleport nodes keep Teleport as their way in. The direct
  ports stay published: the bastion is an extra path, not the only one.

## Client export
With the jump part on, the profile has one extra `<login>-jump` entry per key-login node: the login's private address and port 22 with a
`jumpHost` block (address, port, user, `private-key`), the shape the client's profile type defines. The key path and any secret are never
exported; the client keeps the key. Teleport entries get no jump host (the client cannot use one with Teleport). Issue 415.

## Controller and Teleport
With Teleport on, the controller is a Teleport node named `controller` (kind `controller`; its host certificate also names `slurmctld`).
It starts before Teleport is ready, so `benchctl up` runs `teleport-ssh` in it afterwards and reloads sshd. Teleport only adds a way in:
sshd's `AllowUsers` still limits who gets a shell. Issue 416.

## Not built (open)
- The Infrastructure architecture drawing (hand-laid SVG) has no jump or controller box yet; the Traffic & Communication diagram,
  Connections and the graph model do.
- Memory measured once (`benchctl measure`, 1 CPU node, five forwarded sessions): the jump host peaked at 1.9 MB (cap 24 MB), and slurmctld with the controller's sshd
  showed no difference from slurmctld without it beyond run noise (about 37 MB against 39 MB peak); the controller's +24 MB cap is headroom for sessions, not use.
