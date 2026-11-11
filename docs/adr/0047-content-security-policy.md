# ADR 0047: The page is served under a strict Content-Security-Policy

Status: accepted. Part of the UI redesign (epic 491), follow-up; issue 517.

## Context

The page shows text that comes from the mocks and the containers. It escapes that text (`esc`, DOM nodes), has no inline script and no inline event handler. A policy that forbids inline script and style is the second line of defence if an escape is ever missed.

## Decision

- `GET /` and `/static/*` carry `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`. No `unsafe-inline`, no `unsafe-eval`. It is a pure ASGI wrapper (`CSPMiddleware`), so the event streams are not buffered.
- `/help` pages keep their own inline stylesheet and are left out for now.
- Inline `style=` attributes are gone. Fixed styles became utility classes (`u-*` in `app.css`). Dynamic sizes travel as `data-w`, `data-flex`, `data-p` and `data-bg` and are applied by `paintDyn` through the CSSOM, which the policy allows. The SVG helper `sv` sets `style` through `style.cssText`.
- `tests/test_webui.py` asserts the header and that no inline style or handler returns. `scripts/ui_shots.py` keeps the policy on its first page, visits each screen and fails on any "Content Security Policy" console message; its other pages bypass it because the test hook evaluates strings.

## Consequences

A new template that writes `style="..."` fails the test and the browser check. The monitoring client, which reads the API and not the page, is unaffected.
