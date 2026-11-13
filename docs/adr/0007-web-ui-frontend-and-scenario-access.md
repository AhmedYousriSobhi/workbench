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

## Addendum (issue 492): the page is native ES modules
The single file outgrew "about 100 lines": it was 2,389 lines with 136 functions in one global scope, and nothing but screenshot checks
exercised it. The decision above (no framework, no build step, no CDN, nothing third-party) stands; the file is now split:

- `webui/static/index.html` is markup only, with one stylesheet (`app.css`) and one entry script, `<script type="module" src="/static/js/main.js">`.
- `webui/static/js/` has one module per area (governor, cluster, plan, scenarios, nhc, connections, node, diagram, traffic, shell, ...).
  The browser loads them as they are: no bundler, no transpiler, no minifier.
- Values that change at run time live in one object, `S` in `state.js` (a module cannot assign to a binding it imports); constants and
  functions stay in the module that owns them. Each module exports an `init()` for its event wiring and `main.js` runs them in the
  order the old script ran them.
- `GET /static/{file}` serves only `.js` and `.css` files directly under `webui/static/`, behind the same token as the page, with
  `Cache-Control: no-cache` so an upgrade is never served stale.
- Tests: `node --test "webui/tests/js/*.test.mjs"` (Node 22 or newer; run by `validate.sh`; no packages) covers the pure logic and
  checks that every module links and loads. The browser checks (`scripts/ui_shots.py`, `scripts/doc_shots.py`) set `window.__WB_TEST`,
  and `main.js` then loads `testhook.js`, which puts every export and every field of `S` on `window`; a normal visit exposes nothing.
- The split makes a Content-Security-Policy possible (no inline script or style left); adding it is a separate change.

## Revisit when (modules)
A view needs lazy loading to keep the first paint fast, or the number of modules makes a bundler cheaper than many requests (it does not
on localhost).


## Addendum: login without the token in the URL (issue 466, SEC2)
`/login?token=` put the long-lived token into browser history and any log that saw the URL. It is gone. `benchctl open` (and the
test and screenshot scripts) ask the running page for a one-time code (`POST /api/login-code`, authorised by the token in a header,
60 s, used up on first use) and print `/login?code=...`. Without a link, `GET /login` shows a form that posts the token in the body.
The cookie is unchanged. The page prints no secret, on a terminal or in the log.
