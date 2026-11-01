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
  `benchctl add invite <user>` prints the one-time browser link that sets a password for `tsh login`. Certificate TTL is a `sim.yaml` value;
  a scenario expires it mid-session, and `teleport-outage` stops the proxy.
- **Version:** configurable in `sim.yaml`, default the current 18.x Community Edition image from
  `public.ecr.aws/gravitational/teleport-distroless`, pinned by tag (and digest once measured). Teleport supports a
  client or agent one major version behind its Auth and Proxy services and does not support a newer one, so the
  server MUST be at least the client's `tsh` major. 15.x (the last Apache 2.0 images) is therefore only useful with an
  old `tsh`; `benchctl doctor` warns when the local `tsh` major is newer than the configured server.
- **Licence:** Community Edition images from 16 on are under Teleport's Community Edition licence (individuals and
  companies under 100 employees and 10 million USD revenue; no resale or embedding). The repository only names the
  image tag and never redistributes the binary. Teleport's source stays AGPLv3.

## Consequences
- The login node and nodes need `TrustedUserCAKeys` and a host certificate: both generated into
  `compose/generated/secrets` (gitignored) and mounted read-only; node-base `sshd_config` gains those two lines
  only when the files exist.
- Memory: auth and proxy in one process, budget to be measured in Phase 0 before it joins `min` or `std`
  (priority class P2).
- Open until measured live: the real memory use, the exact 18.x tag, and whether the agentless connection works
  through the unprivileged node network without extra routes.
