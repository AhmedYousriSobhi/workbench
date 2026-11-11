# Web UI: drawing on a canvas (tokens and wires)

For contributors working on the page's diagrams. The visual language comes from the redesign
([brief](../design/ui-redesign/DESIGN_HANDOFF.md)); the decisions are in [ADR 0038](../adr/0038-redesign-canvas-tokens-and-wire.md).

## Colours and fonts

Use the CSS variables from `webui/static/canvas.css`, never the hex values: `--cv-ground`, `--cv-surface`, `--cv-text`,
`--cv-text-muted`, `--cv-error`, `--cv-no-current`, and one per plane: `--plane-access`, `--plane-control`, `--plane-k8s`,
`--plane-data`, `--plane-monitoring`, `--plane-management`. Give the element that holds a diagram the class `canvas` (dark dot-grid ground,
Inter) and put addresses, ports and counts in `.mono` (JetBrains Mono). Text must stay at 4.5:1 on the ground; the tokens do.

## Adding a wire

```js
import { buildWire, updateWire } from './wire.js';

const g = buildWire({ d: 'M0 8 L79 8', plane: 'access', rpm: 12, errors: 0, label: 'Login to slurmctld' });
svgElement.append(g);                      // an <svg> that sits on a .canvas
updateWire(g, { d, plane: 'access', rpm: 60, errors: 2, label });   // on every poll: same node, nothing rebuilt if nothing changed
```

- `plane` is one of the six planes. `rpm` is requests a minute (0 or missing draws the dashed "no current" wire). `errors` adds the red dash.
- The wire gets a text title ("Login to slurmctld: Access wire, 60 requests a minute, 2 errors"), so state is never colour alone. Give it a `label`.
- A part that needs attention: `markAttention(el, 'drained · XID 79')` (it breathes amber and always shows the text); `clearAttention(el)` removes it.
- Do not write your own `@keyframes` for a wire. If you need another dash pattern, add a rule in `canvas.css` with `--period` equal to dash + gap; the
  token test fails otherwise (a period that does not match makes the dots jump at each loop).
- Animation stops under reduced motion and in a hidden tab by itself; nothing to do in your code.

## Checking it

```bash
node --test "webui/tests/js/*.test.mjs"            # the wire's decisions, the token contrast, the dash-period rule
.venv/bin/python scripts/ui_shots.py               # in a real browser: animates, stops under reduced motion, pauses when hidden, fonts load
```
