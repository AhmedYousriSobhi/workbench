# 0023. Login nodes: a custom number, each with its own way in

Status: accepted (issue 189)

## Context
The cluster had one login node, reached by direct SSH, and Teleport (ADR 0020) was added next to it. Testing Teleport
against the only login node means that node also accepts direct SSH, so a client cannot tell whether it really went
through Teleport. Real clusters also have several head nodes with different access.

## Decision
- **A list in `sim.yaml`, `login:`**, one auth mode per login node, default one `ssh` node (nothing changes for existing
  setups). The workbench selection carries the same list (`login_nodes`), so a flag, the Cluster architecture sheet and a
  saved workbench file all say the same thing.
- **Modes:** `ssh` (key login on a port published on 127.0.0.1), `teleport` (no published port, no key login, only a Teleport
  certificate gets in), `both`.
- **Names and addresses:** the first node stays `login` (10.141.255.254, port 2222) so nothing existing breaks; then `login2`,
  `login3`, ... up to 8. Addresses count down from .252 (.253 is the network gateway), clear of the service block; direct
  ports count up from 2222 by position. Every login node is also on the BMC network (jump host), as before.
- **One module**, `benchctl/logins.py`, owns names, modes, validation, ports and the consistency rule (a Teleport login needs
  the Teleport part). Addressing, compose, Teleport registration, the client profile, the Connections tab, completion, the
  governor's plan check and the sheet read it.
- **How a `teleport` login refuses keys:** `images/login/login.sh` installs an empty `authorized_keys` when
  `WORKBENCH_LOGIN_AUTH=teleport`, and only `teleport` and `both` logins get the Teleport secrets mounted, so `sshd` trusts the Teleport CA
  only where the mode says so. Only those logins are registered in Teleport.
- **Declaring them:** `--login ssh,teleport,both` on `start`, `up`, `apply`, `plan` (Tab completes the modes, also after a comma);
  a `teleport` or `both` mode turns the Teleport part on by itself. The sheet has a Login nodes row (add, change the mode,
  remove the last one).
- **The client profile export** uses the first login that takes a key; with none it refuses with one message.

## Consequences
- `--access teleport` alone no longer registers the default `ssh` login in Teleport: say `--login both` (or `teleport`) for that.
- Each login node costs the 40 MB budget of the first.
- Reducing the number removes the extra containers on the next `up`.
- Open until run live: the `tests/real/test_teleport_real.py` access matrix (key accepted on ssh/both, refused on teleport).
