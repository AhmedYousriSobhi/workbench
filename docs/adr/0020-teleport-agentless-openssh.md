# 0020. Teleport access: real Teleport, agentless OpenSSH nodes

Status: accepted (issue 39)

## Context
The application under test reaches cluster nodes through Teleport (`tsh login`, then `tsh ssh` or an OpenSSH client
through the Teleport proxy). Issue 39 left four decisions open: access types, real binary or mock, auth and certificate
TTL, and the node model. This ADR checks each against Teleport's own documentation (goteleport.com/docs: Docker
installation, agentless OpenSSH, upgrade overview).

## Decision
- **Access types:** SSH to the login node and the compute and GPU nodes. Kubernetes, web apps and database access are
  not built (SPEC section 11 keeps them listed).
- **Real Teleport, not a mock.** `tsh login`, short-lived user certificates, the proxy and the OpenSSH `ProxyCommand`
  flow are the behaviour a client depends on, and a mock cannot reproduce them. One container runs
  `teleport --roles=proxy,auth` (the layout the Docker guide gives for testing, config from `teleport configure`),
  unprivileged and memory-limited, with a fixed address from the addressing plan and ports 3080 (proxy and web) and
  3025 (auth) published on 127.0.0.1 only.
- **Agentless OpenSSH nodes, no Teleport process on a node.** Teleport's documented way to register an existing `sshd`:
  the proxy asks the Teleport CA for a short-lived certificate, `sshd` trusts only that CA
  (`TrustedUserCAKeys`), and the proxy opens its own connection to `sshd` on port 22. Registration needs no binary on the
  node: a `kind: node, sub_kind: openssh` resource (`spec.addr`, `spec.hostname`, optional UUID name) created with
  `tctl create`, the CA fetched from the proxy's `/webapi/auth/export?type=openssh`, and a host certificate from
  `tctl auth sign --format=openssh`. `benchctl up` generates the resources from `sim.yaml`, so node names and addresses
  agree with everything else. The docs describe one registration per `sshd` host and do not describe one agent for many
  nodes, so the earlier "one agent in front of sshd" idea is dropped. The cost is one process for the whole cluster,
  not one per node.
- **Known gap against a real cluster:** the docs list what the Teleport agent adds over agentless mode (dynamic labels,
  session recording that survives the connection, session sharing). Those are not simulated; client-testing lists them.
- **Auth:** local users and roles from `sim.yaml` (SSO mock is out of scope). Teleport documents no scripted password
  setup, so each user gets an identity file (`tctl auth sign --user --ttl`, Teleport's documented automation path) and
  `benchctl add invite <user>` prints the one-time browser link that sets a password and a one-time-code (OTP) authenticator for `tsh login`: Teleport 18 cannot disable MFA (`second_factor: off` is
  refused at start; `second_factors: [otp]` is its documented setting), so the sim keeps OTP. Identity files need neither. Certificate TTL is a `sim.yaml` value;
  a scenario expires it mid-session, and `teleport-outage` stops the proxy.
- **Version:** configurable in `sim.yaml`, default the current 18.x Community Edition image from
  `public.ecr.aws/gravitational/teleport-distroless`, pinned by tag (and digest once measured). Teleport supports a
  client or agent one major version behind its Auth and Proxy services and does not support a newer one, so the
  server MUST be at least the client's `tsh` major. 15.x (the last Apache 2.0 images) is therefore only useful with an
  old `tsh`; `benchctl doctor` warns when the local `tsh` major is newer than the configured server.
- **Licence:** Community Edition images from 16 on are under Teleport's Community Edition licence (individuals and
  companies under 100 employees and 10 million USD revenue; no resale or embedding). The repository only names the
  image tag and never redistributes the binary. Teleport's source stays AGPLv3. Per Teleport's 2024 announcement the free tiers are
  personal use and companies under 100 employees and 10 million USD revenue; the thresholds are unconfirmed for 2026 (ADR 0083 addendum).

- **Diagnostics port:** `teleport.diag_addr` on 3434, published on 127.0.0.1 only (ADR 0022): `/healthz` is the page's
  status probe and `/metrics` carries `teleport_connect_to_node_attempts_total`, the counter behind the diagram's dots.

## Consequences
- The login node and nodes need `TrustedUserCAKeys` and a host certificate: both generated into
  `compose/generated/secrets` (gitignored) and mounted read-only; node-base `sshd_config` gains those two lines
  only when the files exist.
- Memory: auth and proxy in one process, budget to be measured in Phase 0 before it joins `min` or `std`
  (priority class P2).
- Open until measured live: the real memory use, the exact 18.x tag, and whether the agentless connection works
  through the unprivileged node network without extra routes.

## Addendum: the cluster identity survives a rebuild (issue 223)
A new Teleport cluster has a new CA, so a client's saved session fails with `tls: unknown certificate authority` and
every user must be invited again. `benchctl down`, `up` and `add invite` therefore copy the sqlite backend (CA, users,
credentials; via the sqlite backup API, safe while Teleport runs), `host_uuid` and the proxy certificate to
`compose/generated/secrets/teleport/state` with the Teleport version. An empty data directory restores it when the
version matches; another version starts a new cluster. The copy is taken at those moments, not continuously.

## Addendum: benchctl pins the proxy certificate (issue 864)
The proxy keeps its self-signed certificate (no `https_keypairs`). Teleport makes it at its first start in
`data/webproxy_cert.pem`, as its own CA (CA:TRUE), naming the private IP, `teleport.workbench.test` and 127.0.0.1.
benchctl no longer skips verification toward it:

- **Pin:** `bootstrap` copies that file from the data folder on disk (written by the container, never taken from the
  network) to `compose/generated/secrets/teleport/proxy_ca.pem`. The context that fetches the OpenSSH CA key trusts only
  that certificate, with the name checked (`teleport.proxy_context`).
- **Messages (no sudo):** a server on the proxy port that presents another certificate is refused (check the port, then
  `benchctl down` and `benchctl up --with teleport`); a missing file has its own message (`benchctl up --with teleport`).
- **Rebuilds:** the certificate is saved and restored with the rest of the state (addendum above), so the pin stays valid.
- **Clients unchanged:** `tsh` and clients still pass `--insecure` (the client profile's `insecure: true`), because the
  certificate is in no system trust store.
- **Status:** the owner's choice is still open (ADR 0090); this is option (1). The live check
  (`tests/real/test_teleport_real.py`, a wrong certificate is refused) is written, not yet run.
