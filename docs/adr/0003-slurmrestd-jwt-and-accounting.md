# ADR 0003: slurmrestd with JWT, accounting on tmpfs, no seccomp loosening

Status: accepted

## Context
`slurmrestd` calls `unshare(2)` at start-up for two hardening steps (SysV semaphores, file descriptor table).
Docker's default seccomp profile forbids `unshare` without `CAP_SYS_ADMIN`, and ADR 0002 rules out extra
privileges. A bad token reaching `slurmrestd` must also never return data.

## Decision
- `SLURMRESTD_SECURITY=disable_unshare_sysv,disable_unshare_files` skips only those two steps. The container
  keeps its own IPC namespace and Docker's default seccomp/AppArmor stay on; no capability is added.
- `SLURM_JWT=daemon` with `-a rest_auth/jwt`: every request needs a token; Slurm refuses tokens for `root`.
  Verified live: no token gives 401; bad, forged or wrong-user tokens get no data (HTTP 511 / `responding: false`).
- The JWT key is made by `benchctl up` (issue 465, below; it used to be baked into the image), owned by `slurm`, group `slurmjwt` (mode 0640),
  with only `slurm` and `slurmrestd` in that group. HS256 verification needs the key, so a `slurmrestd`
  compromise exposes it; acceptable for a closed lab network, never reuse outside it.
- `slurmdbd` also accepts JWT (`AuthAltTypes`), otherwise `slurmrestd` cannot open its accounting connection.
- Simulated users (`sim.yaml`) are created on every Slurm container with fixed UIDs (`WORKBENCH_USERS`).
- MariaDB keeps its data on a 100 MB tmpfs with a small buffer pool and redo log; its container limit
  (250 MB) covers tmpfs plus server. Secrets live only in `compose/generated/secrets` (gitignored, mode 0600/0700);
  the DB password is generated once and kept across regenerations.

## Consequences
- `slurmrestd` runs without two of its own sandboxing layers; the container boundary replaces them.
- Re-check `SLURMRESTD_SECURITY` options and the JWT key handling when bumping Slurm.

## Addendum: keys are made at `up`, not baked into the image (issue 465, SEC1)
The munge key and the JWT key no longer exist in any image layer, so a published base image publishes no key and no two clones share one.
`benchctl up` makes both once in `compose/generated/secrets/slurm` (`munge.key` 1024 random bytes, `jwt_hs256.key` 32; folder 0700, files 0600,
gitignored, kept across `up`s) and mounts the folder read-only at `/run/workbench/slurm` into every container on the node-base, slurm-ctl and
login images. The entrypoint installs them with the owners Slurm needs (`munge:munge` 0400, `slurm:slurmjwt` 0640) before it starts munged,
and exits with one message when the mount is missing. An existing cluster gets keys on its next `up`; the containers are recreated, so every
node and the controller change keys together. Delete the folder and run `up` to rotate them.
