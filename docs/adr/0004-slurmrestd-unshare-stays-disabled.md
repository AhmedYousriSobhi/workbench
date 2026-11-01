# ADR 0004: slurmrestd keeps its unshare steps disabled (monitored exception)

Status: accepted (owner decision, issue 40, option A)

## Context
`slurmrestd` calls `unshare(CLONE_SYSVSEM)` and `unshare(CLONE_FILES)` at start-up. Docker's default seccomp
profile only allows `unshare` with `CAP_SYS_ADMIN`. Options considered:
- A. skip those two steps with `SLURMRESTD_SECURITY=disable_unshare_sysv,disable_unshare_files`;
- B. ship a custom seccomp profile (Docker's default plus a rule allowing `unshare` with only those two flags);
- C. add `SYS_ADMIN` (rejected in ADR 0002).

## Decision
Option A. The two calls only reduce what the process shares with others; in our container `slurmrestd` is started
with `su ... exec`, so nothing shares its descriptor table or semaphore state and the steps would have almost
nothing to do (an inference; the Slurm source gives no rationale). Option B would add a hand-maintained copy of
Docker's sandbox, which can go stale and miss a Docker security fix; that risk outweighs the benefit.

The exception is monitored, not silent:
- the governor watchdog and `benchctl doctor` report any container whose `SLURMRESTD_SECURITY` contains an option
  other than those two (for example `disable_user_check` or `become_user`);
- `benchctl doctor` lists the accepted exception by name;
- the existing check still flags privileged mode, added capabilities and unconfined seccomp/AppArmor.

## Revisit when
The Slurm version changes, Docker's default profile starts allowing these two flags, or the policy becomes
"keep every upstream hardening whatever it costs" (then do option B: test that only those flags pass and every
namespace flag is still denied).
