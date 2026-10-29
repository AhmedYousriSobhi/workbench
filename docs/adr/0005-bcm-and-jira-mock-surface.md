# ADR 0005: bcm-mock and jira-mock surfaces (provisional defaults for open decisions 2 and 3)

Status: accepted as a default; the owner can override (README section 12)

## Context
README section 12 leaves the BCM surface (CMDaemon REST, `pythoncm`, JSON API) and the Jira features the app needs
undecided, and says each must be resolved before the component is built. The app under test is not known yet.

## Decision
- **bcm-mock** speaks the CMDaemon JSON API shape: `POST /json {"service", "call", "args"}` with `cmdevice`
  (getDevices, getDevice, powerOn/Off/Reset), `cmstatus` (getStatus) and `cmpart` (getCategories). Devices come from
  `sim.yaml`, so names agree with Slurm and the BMC farm. The call names are an assumed subset, not verified against
  a real CMDaemon; `pythoncm` and the REST variant are not provided.
- **jira-mock** (mode `mock`) implements REST v2 (v3 path alias): create/get issue, comment, transitions with
  required-field validation (Resolve needs `resolution`), a JQL subset (`=`, `!=`, `in`, `not in`, `AND`, ignored
  `ORDER BY`), webhooks, and failure modes (429 with Retry-After, 503, slow). Modes `cloud` and `dc` are not built.
- Both publish on `127.0.0.1` only (8081, 8080), run unprivileged and stay in the P2 class.

## Revisit when
The app's real BCM or Jira calls are known: replace the call list or surface, and record real responses as
fixtures (README section 13, contract tests).
