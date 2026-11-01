# ADR 0007: Web UI frontend and how it plays scenarios

Status: accepted (default; overrides SPEC W-1's HTMX + Tailwind wording, owner may object)

## Context
SPEC W-1 asks for HTMX + Tailwind with no client-side build step. Both normally load from a CDN, which would make the
page depend on the internet and on third-party scripts served to a localhost admin tool. W-3 forbids Docker access in
the UI process, so it cannot run the Slurm steps of a scenario (ADR 0006).

## Decision
- One static HTML file with ~100 lines of vanilla JavaScript (EventSource for SSE, fetch for actions) and a small
  hand-written stylesheet. No build step, no CDN, nothing third-party; a test asserts the page references no URL.
  HTMX can be vendored later if the pages grow.
- Login by a random token (`ui_token`, 0600), session in a HttpOnly SameSite=Strict cookie; state-changing requests
  also need an `X-Requested-With` header (CSRF). Bound to 127.0.0.1.
- The UI plays scenarios in-process with `host=False`: HTTP targets run, Slurm steps and `slurm.node_state`
  expectations are skipped and shown as such. A governor socket command for them is a later option.

## Revisit when
The pages need more interactivity than plain JS handles well (then vendor HTMX), or the UI must run Slurm steps.
