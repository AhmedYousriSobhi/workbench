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
- The JWT key is baked into the lab image (like the munge key), owned by `slurm`, group `slurmjwt` (mode 0640),
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
