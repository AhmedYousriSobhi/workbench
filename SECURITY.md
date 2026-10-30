# Security policy

opti-sim is a simulation that runs on a developer workstation. It is not a hosted service and holds no real data.

## Reporting a problem

Please report a vulnerability privately through GitHub: **Security** tab, **Report a vulnerability**
(a private advisory). Do not open a public issue for it. Include what you saw, the commit or version, and the steps to
reproduce; a masked `./scripts/benchctl diag` bundle helps for environment problems.

## What is in scope

- The governor and its control socket, the web page (it must never gain root or Docker access), the container hardening
  rules (unprivileged, memory-limited, no Docker socket), and anything that could leak a token or personal data.
- Credentials and tokens are generated at runtime under `compose/generated/secrets` (gitignored) and must never appear in
  tracked files, commits, issues or pull requests. The repository has a privacy guard for this:
  `scripts/check-privacy.sh`, run by the git hooks (`./scripts/install-hooks.sh`) and by `./scripts/validate.sh`.

## Out of scope

The mock Jira, BMC and BCM services accept unauthenticated requests on localhost by design; they are test doubles.
