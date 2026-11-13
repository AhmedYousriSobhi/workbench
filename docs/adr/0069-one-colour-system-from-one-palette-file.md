# ADR 0069: One colour system, generated from one palette file

Status: accepted by the owner on 2026-11-13 (issue 642). Step 1 is built (palette, generator, `tokens.css`, tests); nothing in the page reads `tokens.css` yet.

## Context
The page, the canvases, the in-page help, the logo, the documentation art and the Grafana dashboards each carry their own hex values (84 distinct in `app.css`, 26 in `canvas.css`, more in `webui/wiki.py` and the JS). The same plane has different colours in `canvas.css` and `traffic.js`, two accents compete (GitHub blue and the brand teal), amber means both "attention" and "Management plane", and the logo teal and amber are below 4.5:1 on light grounds, so they cannot be text or button colours as they are.

## Decision
- **One source, one generator.** `docs/assets/palette.yaml` is the only place a colour is edited. `scripts/make_palette.py` (standard library plus PyYAML, deterministic, no network or clock) writes `webui/static/tokens.css`; `--check` fails when it is stale and `validate.sh` runs it. Ramps are built in OKLCH so steps look even; contrast is WCAG 2.
- **Three tiers.** Primitive ramps (50 to 975), semantic tokens per theme (light, dark, midnight, dim, all from the same names; a missing or unknown name fails the generator), and component rules in `tokens.css`. Code uses semantic names only. Existing CSS variable names are kept where the meaning is unchanged.
- **Logo anchors are fixed** (teal, ink, amber, light and dark) and are for fills, the logo and large graphics. Text and buttons use tuned tokens (`accent_text`, `accent_fill`, `warn_text`): the generator moves the anchor's lightness until the contrast against the theme's surfaces is reached (5.5:1 for these three).
- **Meanings.** Teal is brand, interaction and good (`ok`, with a check mark). Amber is attention only. Red is error only. Real versus simulated is outline and label, not hue. Governor levels are one ramp, teal to amber to red, each also labelled.
- **Planes** get six hues and six line types that are the same in every view; plane colours appear only on lines and labelled chips, never on status dots or node squares.
- **Canvases stay dark in every page theme**; they carry their own resolved set under `.canvas`, and print inverts it to light.
- **Colour reaches elements through data attributes and stylesheet rules only** (`[data-plane]`, `[data-state]`): the CSP is `style-src 'self'`, and `var()` in SVG presentation attributes is not reliable across renderers. Standalone SVGs (documentation art, the logo) get literal values from the generator.
- **Checks in `validate.sh`:** contrast for every pair and theme, colour-blind distances (planes fall back to line types, teal against its neighbours and the chart set are hard minimums), line types at a 1.5 px stroke, states, charts, `prefers-contrast: more`, determinism and the schema.

## Consequences
- A contributor adds or changes a colour by editing `palette.yaml` only.
- Step 2 of the programme (a no-visual-change refactor) must carry today's exact values: it does so with `#rrggbb` literals in the palette, and the new look arrives in a later step by replacing them with ramp references.
- A change that alters what a token means needs an ADR; a value tune does not.
- Later steps (one issue each): the refactor of `app.css`, `canvas.css`, the JS and `wiki.py`; the plane mismatch and data attributes; the new look; the help page, logo script, Grafana, documentation art and the no-flash `theme-init.js` (a `'self'` file, no CSP change); the generated colour guide.
