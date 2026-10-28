# ADR 0002: Slurm 26.05 built from source; sim containers get no extra privileges

Status: accepted

## Context
Ubuntu 24.04 packages Slurm 23.11. That release lacks the August-September 2026 security fixes
(CVE-2026-65107 and related, fixed in 26.05.4, 25.11.8 and 25.05.9) and its `slurmd` insists on creating a cgroup
scope, which needs a writable cgroup tree. Docker mounts that tree read-only; the only workaround we found was
`SYS_ADMIN` plus `apparmor=unconfined`, which was rejected as a security gap.

## Decision
- `images/node-base` builds Slurm 26.05.5 from the SchedMD tarball, pinned by version and SHA256 (bump both
  together). Release 26.05.5 (2026-10-13) was the newest at the time and includes all of the above.
- `cgroup.conf` sets `CgroupPlugin=disabled`, which 26.05 accepts (23.11 did not). `slurmd` then starts with no
  capabilities, no AppArmor override and a read-only cgroup tree. Jobs are synthetic, so no cgroup control is lost.
- Fake GPUs are backed by `/dev/hpcsim-gpuN` symlinks to `/dev/null`, created by the entrypoint.
- `simctl doctor` runs `check_containers`: every container labelled `hpcsim.class` must be unprivileged, have no
  added capabilities or disabled confinement, no host network/pid, no Docker socket and a memory limit.

## Consequences
- Rebuilding Slurm costs a few minutes; watch the SchedMD security announcements and bump the pin.
- Any future need for extra container privileges requires an ADR exception and shows up as a doctor failure
  until the check is adjusted on purpose.
- The checksum comes from the same origin as the tarball, so it guards against corruption, not a compromised mirror.
