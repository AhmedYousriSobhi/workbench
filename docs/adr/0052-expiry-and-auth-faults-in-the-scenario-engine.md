# ADR 0052: Expiry and authentication faults in the scenario engine

Status: accepted (owner decisions of 2026-11-12). Issue 549; epic 557 (config lab A-15).

## Decision

One scenario target, `auth` (`benchctl/authfaults.py`), makes the real component answer with its real error. It needs the host CLI (Docker, openssl), so the page does not play it.

- `slurm_token_ttl: {user, ttl}` issues a Slurm JWT with `scontrol token ... lifespan=N` and saves it to `compose/generated/secrets/slurm_token` (mode 600, never printed). There is no global setting for the lifespan of future tokens in Slurm; a token that carries its own `exp` is the real mechanism.
- `teleport_cert_ttl` / `teleport_cert_restore` re-sign a user's identity file with `tctl auth sign --ttl`; restore uses `teleport.cert_ttl` from `sim.yaml`. The Teleport outage half of issue 209 stays there.
- `munge_wrong_key` / `munge_restore` swap one node's munge key and restart munged inside the node. The node's own key is kept in the node (`munge.key.good`, `cp -n`, so a second fault keeps the first backup) and restored from there, never from the image: keys generated at `up` (issue 465) stay safe.
- `bmc_tls_expired: true|false`: the farm holds a second certificate made at image build with the same key and name and a validity that ended yesterday; `POST /sim/tls` reloads it into the running TLS context. No restart.
- `k8s_cert_expired: true|false`: the API server's serving certificate is replaced by one signed by the same CA that ended yesterday (made with `openssl ca -startdate -enddate`, because `openssl x509` only takes dates from 3.4) and the API server restarts; the good one is kept as `apiserver.crt.good`.

## Addendum (2026-11-14, issue 636)
`get faults` now reads `teleport_cert_ttl` back (`benchctl/faultstate.py`): a simulated user's identity file whose SSH certificate has ended is reported as an auth fault on `teleport`. Only the certificate line is read, for its `valid before` date; a file that cannot be parsed is skipped, never reported.

## Consequences

A scenario that stops half-way leaves the fault in place; every fault has a restore step. The other control-plane components also reject the expired certificate until restore. Unit tests cover the commands and, with real openssl, the dates; the live test covers the BMC farm, munge and the token; the Teleport, Kubernetes and slurmrestd 401 paths need `--with teleport`, `--with k8s` and the accounting group.
