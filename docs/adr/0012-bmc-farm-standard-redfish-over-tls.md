# 0012. bmc-farm also serves standard Redfish over TLS

Status: accepted

## Context
Real Redfish clients address one BMC as `host:port` over HTTPS, pin its certificate, log in
with `POST /redfish/v1/SessionService/Sessions`, and follow `@odata.id` links from `/redfish/v1`. bmc-farm served one
process under `/<node>/redfish/v1` over plain HTTP, with no collections, sessions, firmware or accounts. The web page,
scenarios and tests use that path form.

## Decision
Keep the HTTP surface on `127.0.0.1:8000` unchanged and add a TLS surface on `127.0.0.1:8443` served by the same process:
- Standard paths (`/redfish/v1/...`, links without the node prefix). The node is chosen by the `Host` header
  (`bmc-<node>` or `<node>`, port ignored; any other host gets the first node), so one listener stands in for N BMCs.
  The `bmc-farm` container carries a network alias `bmc-<node>` per node, so a client on the compose network (the
  login node, ADR 0011) dials `bmc-cn001:8443` as it would a real BMC address.
- Collections for Systems, Chassis, Managers, LogServices, firmware inventory, accounts and sessions; the service root is
  open, everything else needs a session token or Basic auth.
- One read-only account (`admin`, random password) generated once by `simctl up` into
  `compose/generated/secrets/bmc_credentials` (the container runs as `nobody`, so the file is 0644 inside a 0700 folder).
  With no file, auth is off (unit tests, standalone runs).
- A self-signed EC certificate made at image build (stable for one build, so a pinning client keeps trusting it); the
  build stage needs `cryptography`, the runtime image does not.

## Consequences
- Fault injection (`/sim/inject`) shows on both surfaces. TLS requests count toward `/sim/stats`.
- Direct host access needs a `Host` header or `curl --resolve`; the first node answers otherwise.
- Real BMC quirks (slow responses, vendor shapes, `ThermalSubsystem`) are not simulated; only the legacy
  Thermal/Power shape is.
