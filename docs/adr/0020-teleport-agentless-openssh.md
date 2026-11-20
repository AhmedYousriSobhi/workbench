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
- **Status:** the owner approved this option (1) on 2026-11-20 (recorded in ADR 0090). The live check
  (`tests/real/test_teleport_real.py`, a wrong certificate is refused) is the one that proves it.
- **To change it later:** edit `proxy_context` and `bootstrap` in `benchctl/teleport.py`, the matching unit tests and the live
  test above; ADR 0090 lists what going back to skipping would take. The three `check_hostname = False` lines in
  `benchctl/k8s.py` are a different, smaller question (the chain is already checked) and are not decided here.

## Addendum: Community Edition licence and SSO limits, read from primary sources (issue 249, 2026-11-20)
Research only: no decision above is changed. Items that contradict or go beyond ADR 0020 are listed for the owner at the end.
Sources were read on 2026-11-20 and are the Teleport documentation, the licence file in the Teleport repository and
Teleport's 2024 announcement. No cluster was used.

### Licence
- **Three licences apply, to different things.** (1) Source in the GitHub repository: the repository's `LICENSE` is the
  "GNU AFFERO GENERAL PUBLIC LICENSE, Version 3, 19 November 2007", and the GitHub API reports `AGPL-3.0` for
  `gravitational/teleport`. The announcement says: "The license of our open source repository remains AGPLv3 with no
  changes." (2) Official binaries and images from Teleport 16 on: the Teleport Community Edition License,
  `build.assets/LICENSE-community` on `master` (https://github.com/gravitational/teleport/blob/master/build.assets/LICENSE-community).
  The feature matrix (https://goteleport.com/docs/feature-matrix/) says: "Commercial for binaries, with restrictions:
  Free usage for companies with <100 employees and <US$10M annual revenue. Code on GitHub distributed via AGPL-3.0".
  (3) Releases before 16: Apache 2.0. The announcement (https://goteleport.com/blog/teleport-community-license): "We will
  stop distributing compiled binaries, container images, and Amazon Machine Images" under Apache 2.0, starting with
  Teleport 16; "Companies or individuals are welcome to compile the source code under AGPLv3 terms"; client libraries and
  documentation stay Apache 2.0.
- **The thresholds are confirmed in the licence text itself** (current `master`): "THE TELEPORT COMMUNITY EDITION IS
  AVAILABLE ONLY TO ORGANIZATIONS THAT HAVE LESS THAN ONE HUNDRED (100) EMPLOYEES AND LESS THAN TEN MILLION DOLLARS
  ($10,000,000.00) IN ANNUAL REVENUE." Section 1: "Legal Entity" means "an organization that has less than one hundred
  (100) employees and less than Ten Million U.S. Dollars ($10,000,000.00) in annual revenue." Section 2 makes it a
  condition: "the requirement that the party exercising this license must be either an individual or a Legal Entity is an
  express condition to the grant ... If the conditions of this License are not met, no grant of license under this
  Section 2 exists." Both limits apply (announcement: "Companies larger than 100 employees or more than $10MM AR need to
  contact our sales team"). The "unconfirmed for 2026" remark above and in the ADR 0083 addendum is therefore resolved:
  the same thresholds are in the file today.
- **Individuals:** the announcement says individuals "can use Teleport Community Edition for personal and hobby use with
  no restrictions" and that the change affects you only "if you are a company using Teleport Community Edition". The
  licence file does not repeat this: its text speaks of "an individual or Legal Entity" in section 2 but defines only
  Legal Entity and opens "AVAILABLE ONLY TO ORGANIZATIONS". The personal-use statement is from the announcement, not from
  the licence text.
- **Redistribution (section 4):** "You may reproduce and distribute copies of the Work or Derivative Works thereof in any
  medium ... provided that You ... give any other recipients ... a copy of this License". It does not forbid resale or
  embedding in the main terms. The only text on "commercial offerings", "redistribute" and "embed" is in the added
  "CRITICAL SECURITY EXCEPTION" paragraph, which covers companies over the thresholds using designated security patch
  versions: they "may deploy the software solely for internal operations and may not incorporate it into commercial
  offerings, redistribute it, or embed it within products or services offered to third parties."
- **A Legal Entity is "an organization": whether the owner's organisation is under both thresholds is not stated in the
  repository and is not something the sources can answer.** Not found: any Teleport text on whether a test or demo
  deployment that uses no production data counts differently for a company; the licence has no evaluation or testing
  carve-out (section 2 grants to "You", a Legal Entity or individual meeting the definition).
- **What this repository does (facts, not a ruling):** it names an image tag and a `sim.yaml` version and never copies the
  binary or image into git or redistributes it (this ADR, Licence bullet). Each user pulls the image from
  `public.ecr.aws/gravitational/teleport-distroless` and so accepts the licence for themselves. Whether a user's
  organisation qualifies is that user's question. Section 4(a) would apply only if the repository started to ship the
  binary or an image. Using Teleport 15.x (Apache 2.0) or a self-compiled AGPLv3 build avoids the Community Edition
  licence, with the costs below.

### SSO and identity
- **Community Edition supports GitHub only.** Get Started with SSO (https://goteleport.com/docs/get-started/sso.md):
  "Teleport Community Edition only supports GitHub as an IdP, and you can only connect GitHub manually." The feature
  matrix, row Single Sign-On: Enterprise lists "GitHub, Google Workspace, Microsoft Entra ID, Okta, SailPoint, OIDC, SAML,
  Teleport"; Community lists only "GitHub". The FAQ (https://goteleport.com/docs/faq/) says of OIDC, SAML and Active
  Directory: "Teleport offers this feature for the Enterprise (Cloud) and Enterprise (Self-Hosted) versions of Teleport."
  The same SSO page says any IdP that "supports the SAML or OIDC protocols" can be connected, which per the matrix is an
  Enterprise feature. An Active Directory or LDAP row is not in the feature matrix; the FAQ line above is the only
  primary statement found on Active Directory.
- **GitHub connector limit in Community Edition** (https://goteleport.com/docs/zero-trust-access/sso/integrate-idp/github-sso.md):
  "In Teleport Community Edition, this organization must not have external SSO set up, or Teleport will refuse to create
  the GitHub authentication connector." Usernames become GitHub usernames, and "GitHub usernames are not formatted as
  email addresses".
- **Consequence for an AD-backed identity plane:** it cannot federate into Teleport Community Edition (no OIDC, no SAML,
  and GitHub SSO is refused for a GitHub organisation that uses external SSO). Local users, which ADR 0020 already uses,
  are not a licence-gated feature in any page read. What ADR 0020 assumes (local users and roles from `sim.yaml`, "SSO
  mock is out of scope") is consistent with these pages.
- **Other rows relevant to ADR 0020** (feature matrix): "Agentless Integration with OpenSSH Servers" is available in all
  three editions; "Session Recording with Playback" and "Enhanced Session Recording" in all three; "Per-Session MFA" in
  all three; "Hardware Private Key Support" is not in Community; "Kubernetes Clusters" are in all three.
- **Not found:** an Enterprise price or trial terms for a public test repository; a statement of whether the
  Community Edition licence changes between 18.x patch releases (the file read is `master`, the 2024 copy at
  https://scancode-licensedb.aboutcode.org/teleport-ce-2024.LICENSE lacks the security exception paragraph, so the file
  has changed since 2024); the licence text shipped inside the 18.11.8 image (the image was not pulled).

### Where ADR 0020 is contradicted or unsupported (for the owner; nothing changed)
1. "no resale or embedding" in the Licence bullet is not in the Community Edition licence main terms (section 4 permits
   redistribution with the licence attached). The words appear only in the security-exception paragraph, for over-threshold
   companies. The bullet is therefore unsupported as a description of the licence.
2. "Per Teleport's 2024 announcement ... thresholds are unconfirmed for 2026": now confirmed from the licence file on
   `master`. ADR 0083 has the same sentence and ADR 0084 section on references states the thresholds.
3. The ADR names "individuals and companies under 100 employees and 10 million USD revenue"; the licence text conditions
   every grant on being an individual or a Legal Entity under both limits, and the personal-use claim rests on the
   announcement only.
4. Not contradicted: AGPLv3 source, Apache 2.0 for 15.x and earlier, no SSO beyond GitHub in Community Edition, agentless
   OpenSSH available in Community Edition.

### Decisions for the owner
- Does the owner's organisation fit under both thresholds, and may a public test target depend on Community Edition? If
  unclear, ask Teleport sales (a Teleport statement, not this repository, answers it).
- Keep the current rule (the repository only names the tag; each user accepts the licence), or add a licence notice to
  `benchctl add`/`up` output and the getting-started guide.
- If Community Edition is not acceptable: alternatives are Teleport 15.x with an old `tsh` (Apache 2.0, ADR 0020 version
  bullet), a self-built AGPLv3 binary (ADR 0083 does not cover the build cost), or plain OpenSSH user certificates
  signed by a CA with no Teleport (loses `tsh login`, the proxy and the audit log the client depends on).
- Whether SSO federation (AD) should be tested at all: with Community Edition only GitHub SSO exists, so an AD-backed
  plane would need Enterprise or a separate component outside Teleport.
