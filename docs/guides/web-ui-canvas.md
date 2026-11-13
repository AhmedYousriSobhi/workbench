<p align="center"><img src="../assets/art/guide-web-ui-canvas-hero.svg" alt="Tokens, a wire and a zoom level" width="720"></p>

# Web UI: drawing on a canvas (tokens and wires)

**In 30 seconds:** for contributors: draw page diagrams with the `--cv-*` and `--plane-*` CSS variables, never hex values, and add a wire with the one recipe in `wire.js`.

## Do it

```bash
node --test "webui/tests/js/*.test.mjs"      # the page's logic tests
```

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

## The Cluster level (the Circuit view)

`webui/static/js/circuitlayout.js` says where every part and wire of one cluster goes (pure data, tested without a browser); `circuit.js` draws it into
`#flow` and keeps it current. To add a part or a wire, add it to `circuitSpec` (same coordinates for every cluster: a part the cluster lacks is
greyed through `present: false`, never left out) and name the documented link a wire stands for (`link: ['from', 'to']`, a pair in `webui/comms.py`),
or `link: null` for wiring the model does not know, which is then drawn as "declared". Give each part `data-ent` (its id in the declared graph) so
focus, blast radius and the lens work on it. Do not draw a pulse for a link nothing counts.

```bash
node --test "webui/tests/js/circuitlayout.test.mjs"   # same template for every cluster, greyed parts, controller variants, racks, layers
.venv/bin/python scripts/ui_shots.py                    # in a browser: wires, titles, absent parts greyed, one layer isolated at a time
```

## The zoom: Fleet, Cluster and Pool

`zoom.js` owns the level (`levelNow()`: `fleet`, `cluster`, `pool`, `traffic`; `infraLevel()` is the same without Traffic) and the move between levels (`zoomTo(level, {origin, pool, cluster})`, ADR 0040). Fleet
is `fleet.js` + `fleetlayout.js`, Pool is `pool.js` + `poollayout.js` (the pure parts are tested with `node --test`). To show a new fact on a card or
tile add it to the pure module first and say "not measured" where the page has no data; never default a missing number to 0. After any move exactly one
layer is visible and the others are `hidden` and `inert`; `syncZoom()` is the one place that decides that, so call it (not `hidden = ...`) after a state change.

```bash
node --test "webui/tests/js/poollayout.test.mjs"   # paging, filters, KPIs, state bar, GPU bars, card health and structure
.venv/bin/python scripts/ui_shots.py                 # in a browser: one layer shown after opening a rack, Escape, the FLEET stop, opening a card
```

The page's width must not depend on the view: never size `.cols` or `#nodeswrap` from a view's content. `ui_shots.py` does not catch a width jump, so look at Fleet, Cluster and Pool at two window widths after a layout change.

## Traffic analysis

`trafficanalysis.js` (pure, `node --test`) computes rates, river bands, probe readings, periods and findings from `/api/traffic`; `analysis.js` draws them and
polls only while the Traffic tab is visible. To add a reading: add the pure function and its test first, return `null` where the record cannot say, and draw `null`
as "not measured", never 0. Periods are found per stream. `ui_shots.py` feeds the screen synthetic `/api/traffic` data and checks the river, the findings, the hash
round trip and that nothing polls while another tab is open.

## Under the hood

<p align="center"><img src="../assets/art/guide-web-ui-canvas-diagram.svg" alt="Colour tokens, a wire drawn as halo, core and pulse, and the three zoom levels Fleet, Cluster and Pool" width="720"></p>

> [!HOOD]
> Colours come from `webui/static/canvas.css` tokens, pinned by `webui/tests/js/tokens.test.mjs`. A wire is a halo, a core and a moving pulse, one colour per plane (`wire.js`). The page's zoom has three levels, Fleet, Cluster and Pool.
> The Focused wire capture runs only through the governor (`capture-start`, `capture-read`, `capture-stop`): off by default, one wire at a time, at L0 and L1, class P5, ended after 5 minutes or when the panel closes. It never stores a body and never touches Docker from the page (SPEC W-3, ADR 0042).

Source: `webui/static/canvas.css`, `webui/static/js/wire.js`, [ADR 0038](../adr/0038-redesign-canvas-tokens-and-wire.md), [ADR 0041](../adr/0041-per-request-traffic-record.md), [ADR 0042](../adr/0042-on-demand-wire-capture.md)

## Tips

> [!TIP]
> A reading the record cannot give is drawn as "not measured", never as 0.

## If it goes wrong

| Symptom | Try |
|---|---|
| A token test fails | a hex value or a token changed; use the variable, or update the pinned value on purpose |
| The page polls while hidden | live feeds run only while the page is visible (SPEC W-11) |

## Learn more

- [The web page](../manual/05-web-page.md)
- [Client testing](client-testing.md)
