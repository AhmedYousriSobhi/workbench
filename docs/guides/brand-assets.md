# Logo and icons

The mark is a **W of servers on a bench**: teal rounded squares are running nodes, the dashed amber square is the node
the memory governor holds back, and the bench is the workbench. It follows the viewer's light or dark scheme.

| File | Use |
|---|---|
| `docs/assets/logo.svg` | the full mark (README, docs, slides); 64 px and up |
| `docs/assets/logo-small.svg` | the cut for 16 to 32 px: heavier strokes, no legs |
| `docs/assets/favicon-16.png`, `favicon-32.png` | browser tab icons (transparent) |
| `docs/assets/apple-touch-icon.png` | 180 px home-screen icon on a light ground |
| `docs/assets/logo-512.png` | social preview and avatars (transparent) |

Colours: teal `#0b8f80` (dark scheme `#2fc4b2`), ink `#14211f` (`#e5eeec`), amber `#b86e00` (`#f0a030`).

## Change or rebuild it

All files come from one source, `scripts/make_logo_assets.py`. Edit the shapes there, then:

```bash
pip install -e '.[browser]' && .venv/bin/python -m playwright install chromium    # once, for the PNGs
.venv/bin/python scripts/make_logo_assets.py          # rewrites the SVGs and PNGs and the web page's icons
.venv/bin/python scripts/make_logo_assets.py --check  # exit 1 if anything is out of date (validate.sh runs it)
```

The web page shows the same small cut next to its title, painted from its own theme colours, and uses the generated
files for its tab icons.

## Documentation art

Besides the README illustrations, the script draws ten small icons (64 by 64): `tile-*.svg` for the Wiki Home grid and `group-*.svg` for the sidebar groups.

The illustrations in the README, manual and guides are SVG files built by one script, `scripts/make_doc_art.py`, the
same way as the logo: shapes are defined in code, written to `docs/assets/art/`, and nothing is drawn by hand or taken
from elsewhere.

```bash
.venv/bin/python scripts/make_doc_art.py           # rewrites docs/assets/art/*.svg
.venv/bin/python scripts/make_doc_art.py --check   # exit 1 if a file is missing, stale or not generated here (validate.sh runs it)
.venv/bin/python scripts/make_doc_art.py --list    # every file with its size
```

Rules every file follows (`tests/test_doc_art.py` checks them):

- one file works on light and dark GitHub and in the page's help: it carries its own `prefers-color-scheme` styles;
- at most 60 KB, a `<title>` and a `<desc>` that say what the picture shows;
- real text in a system font stack; every text colour is paired with its ground in `TEXT_PAIRS` and reaches 4.5:1 in both themes;
- status is never colour alone (a node has a tick, pause bars or a cross as well);
- motion is a CSS animation inside the SVG (the wire pulse, as in `wire.js`) and stops under `prefers-reduced-motion`; the still frame makes sense alone.

Shared primitives (`node`, `rack`, `wire`, `cloud`, `governor`, `bubble`, `label`, `arrow`) live in the script; plane
colours follow [the web page canvas](web-ui-canvas.md). Add a picture as one function and one entry in `ART`.

## Colours

The logo's teal, ink and amber are the anchors of one colour system (ADR 0069). Every colour lives in
`docs/assets/palette.yaml`; `python scripts/make_palette.py` writes `webui/static/tokens.css` from it and
`validate.sh` fails when that file is stale. To add or change a colour, edit the palette file only, regenerate, and run
`./scripts/validate.sh`: the contrast, colour-blind and line-type tests tell you if the change is readable.

- The three anchors are fixed. They are for fills, the logo and large graphics; text and buttons use the tuned
  `accent_text`, `accent_fill` and `warn_text`, because the light-theme teal and amber are about 4:1 on white.
- Teal means brand, interaction and good (with a check mark); amber means attention only; red means error only.
- `tokens.css` is generated but not yet read by the page: moving the page, the canvases and the help onto it is the
  next steps of issue 642.
