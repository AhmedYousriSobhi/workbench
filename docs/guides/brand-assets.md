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
