# ADR 0038: Redesign canvas tokens, the shared wire and self-hosted fonts

Status: accepted (owner approved the recommendations, 2026-11-11). Issue 494, epic 491, ADR 0007 addendum.

## Context
The redesign (docs/design/ui-redesign/) draws the architecture, the traffic analysis and the infrastructure zoom with one visual
language: a dark dot-grid ground, six plane colours and "wires" that carry pulses of moving dots. The mockups are design-canvas files
with inline styles and a Google Fonts link. Two things in them cannot ship as they are: the page must load nothing third-party (ADR 0007,
a test asserts it, and a CSP needs it), and every wire animates by a fixed 20 units although the wires use dash periods of 20, 16, 12, 9 and 30,
so all but the first jump at each loop.

## Decision
- **Tokens** are CSS custom properties in `webui/static/canvas.css` (`--cv-*` for ground, surfaces, text, error, no-current;
  `--plane-*` for the six planes). Code uses the tokens, never the hex values. Values are the brief's; `--cv-wire-base` (`#141C26`) is
  taken from the mockup, where the brief names no value. `webui/tests/js/tokens.test.mjs` checks contrast (text 4.5:1, plane colours 4.5:1 on
  the ground) and the animation rules on the stylesheet itself.
- **A canvas is always dark**, in every page theme, like a terminal inside a light page: the plane colours are chosen for a dark ground and the
  brief defines no light variants. The page's own themes are untouched. Revisit if the owner wants light canvases (it needs a second set of
  plane colours that still reach 4.5:1 on white; amber and green do not today).
- **One wire component**, `webui/static/js/wire.js`: four strokes (base 7, halo 9 at 12 %, core 1.5 at 55 %, pulse `2 18`), a red `1 60` dash
  for errors, and one dashed grey stroke for "no current". Each animated stroke moves by exactly its own dash period (`--period`), which is
  what makes the loop seamless; a test asserts the period equals the dash length. A wire is `role="img"` with a text title ("Access wire,
  12 requests a minute, 2 errors"), so the state is never carried by colour or motion alone.
- **Speed follows the rate** on a log scale: `4 / (1 + log2(1 + rpm / 6))` seconds per period, clamped to 0.45-4 s; no requests, no motion. The
  brief says only "speed follows the request rate"; this curve is a choice (6 a minute is a slow beat, 60 a steady stream, 600 and up a blur at the
  clamp) and lives in one function, `pulseSeconds`, so it is easy to change. The red dash keeps the pulse's ground speed.
- **Motion rules (W-11, accessibility):** all wire and "needs attention" animation is off under `prefers-reduced-motion: reduce` (the dots stay
  drawn) and paused while the tab is hidden (`<html data-tab-hidden>`, set from `visibilitychange`). `scripts/ui_shots.py` checks both in a browser.
- **Fonts are self-hosted**: Inter and JetBrains Mono, latin subset, variable woff2 (48 KB and 40 KB) in `webui/static/fonts/`, under the SIL Open
  Font License (the licence texts sit beside them and are not served). They come from the `@fontsource-variable` packages (`inter` 5.1.1,
  `jetbrains-mono` 5.1.2). `/static` serves `.woff2` as `font/woff2` behind the token.
- Styles and animation are applied by class and CSS variables, and per-wire values through the CSSOM (`style.setProperty`), never a `style`
  attribute, so a later strict CSP needs no `unsafe-inline` for styles.

## Consequences
Screens 3 to 7 build on `wire.js` and `canvas.css`. The existing page is unchanged: nothing uses the new classes yet, and the new stylesheet (4 KB) and the
fonts (88 KB) change nothing visible; a browser fetches a font only when text uses its face.

## Revisit when
A light canvas is wanted, the rate curve reads wrong against real traffic, or more dash patterns are needed (the mockups' `4 8` heartbeat and
`2 7` certificate wires: add a kind with its own `--period`, not a new keyframe).
