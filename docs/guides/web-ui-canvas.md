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

Use the CSS variables from `webui/static/tokens.css` (generated from `docs/assets/palette.yaml`; a canvas follows the page theme), never the hex values: `--cv-ground`, `--cv-surface`, `--cv-text`,
`--cv-text-muted`, `--cv-error`, `--cv-no-current`, and one per plane: `--plane-access`, `--plane-control`, `--plane-k8s`,
`--plane-data`, `--plane-monitoring`, `--plane-management`. Give the element that holds a diagram the class `canvas` (dot-grid ground in the page theme's colours,
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

`webui/static/js/circuitlayout.js` says where every part and wire of one cluster goes. It is pure data, tested without a browser. `circuit.js` draws it into `#flow` and keeps it current.

**To add a part or a wire:**
1. Add it to `circuitSpec`. The coordinates are the same for every cluster. A part the cluster lacks is greyed through `present: false`, never left out.
2. Name the documented link a wire stands for: `link: ['from', 'to']`, a pair in `webui/comms.py`. For wiring the model does not know, use `link: null`. It is drawn as "declared".
3. Give each part `data-ent` (its id in the declared graph), so focus, blast radius and the lens work on it.

Do not draw a pulse for a link nothing counts.

```bash
node --test "webui/tests/js/circuitlayout.test.mjs"   # same template for every cluster, greyed parts, controller variants, racks, layers
.venv/bin/python scripts/ui_shots.py                    # in a browser: wires, titles, absent parts greyed, one layer isolated at a time
```

## The zoom: Fleet, Cluster and Pool

`zoom.js` owns the level and the move between levels.
- `levelNow()` returns `fleet`, `cluster`, `pool` or `traffic`. `infraLevel()` is the same without Traffic.
- `zoomTo(level, {origin, pool, cluster})` moves between levels (ADR 0040).
- Fleet is `fleet.js` + `fleetlayout.js`. Pool is `pool.js` + `poollayout.js`. The pure parts are tested with `node --test`.

**To show a new fact on a card or tile:**
1. Add it to the pure module first.
2. Say "not measured" where the page has no data. Never default a missing number to 0.

After any move, exactly one layer is visible and the others are `hidden` and `inert`. `syncZoom()` is the one place that decides that. After a state change, call it, not `hidden = ...`.

```bash
node --test "webui/tests/js/poollayout.test.mjs"   # paging, filters, KPIs, state bar, GPU bars, card health and structure
.venv/bin/python scripts/ui_shots.py                 # in a browser: one layer shown after opening a rack, Escape, the FLEET stop, opening a card
```

The page's width must not depend on the view: never size `.cols` or `#nodeswrap` from a view's content. `ui_shots.py` does not catch a width jump, so look at Fleet, Cluster and Pool at two window widths after a layout change.

## The run layer (a scenario on the diagram)

`runlayermodel.js` (pure, `node --test`) turns the run state (`GET /api/scenarios/run`) into the scenario's steps, grouped by their YAML number, one badge per part, the expectation pins and the words for the rail and the strip. `runlayer.js` draws it into `#flow` as `#runlayer`, again after every rebuild of the Circuit view (ADR 0086).

**Where a step lands:**
1. `scenario.touches(step)` in `benchctl/scenario.py` gives the ids: node names, service ids, and the parts `slurmctld`, `slurmdbd`, `slurmrestd`, `login` and `teleport`.
2. The page looks for a node's square (`data-names`), then `data-part`, then `data-ent`. A greyed part counts as not drawn, and the rail says so.
3. For a new target kind, map it in `touches`. If it truly lands nowhere, add it to `UNMAPPED` in `tests/test_scenario_touches.py` with the reason. That list only shrinks.

Badges come from the run state, never from flow events. Each badge has a number and a glyph, and says its state in words in its `aria-label`. Nothing in the layer animates. Colours are tokens only: `--accent`, `--warn`, `--bad`.

**Badge size.** `runlayer.js` sets sizes in screen pixels and turns them into drawing units with the drawing's scale (`getScreenCTM()`): text 15 px (never under 14 px; the Circuit view is drawn at about 0.55 of its 1700 units on a laptop or a phone), a pill about 1.7 times the text, and a transparent `.rl-hit` rectangle of at least 40 px round it for touch. `ui_shots.py` fails on smaller text, a smaller tap target, or two overlapping pills, for `node-down` and for the dense `client-stress` (every node badged).

**Before Play (phase 2).** `previewRun(p)` turns the preview (`GET /api/scenarios/{name}`) into a run that has not started: every step `planned` (or `cli`), nothing reached. `shownRun()` picks what the layer draws: a live run always; else the scenario picked in Test run, as a preview until it has run; else the last run. The ghosts are the same badges with the class `ghost` (dashed), `data-runghost` gives every touched part a dotted ring, `data-runblast` gives the chosen step's blast parts the red dashes, and `.rl-blast` draws ◎ (role `img`, not a tab stop). Play swaps the preview for the run state of the same name in one redraw; badges keep their parts and order (`ui_shots.py` checks they do not move). Stop pressed on the page sets `runStopHide`; once the run says it stopped, the layer is taken off.

**Replay (phase 3).** `replayStops(groups)` lists the recorded steps in the order they ran. `replayGroups(groups, stops, at)` and `replayPins(pins, stops, at)` give the layer as it stood at stop `at`; `replayText` is the slider's `aria-valuetext`, `replayKey` maps the slider keys. `S.replay` (`{key, at}`) holds the position, and `paintRun()` swaps the groups, so the badges, the ring, the pins and the rail follow one value.

**The player.** `#rs-replay` in the strip holds `#rs-slider` (`role="slider"`, its ticks `aria-hidden`), **Play** (hidden under reduced motion; a 1.2 s step timer, no tween) and **End replay**. After Stop (`runStopHide`) the diagram is plain and `#run-last` offers **Replay the last run** from the page's own run state (memory only). `ui_shots.py` `check_replay` drives it by keyboard on desktop and phone.

**Blast for every target.** `webui/blast.py` `TARGETS` declares, per target kind, the parts a client reads that a step changes, with one sentence; `step_blast(step)` adds the entries of what the step implies (`scenario.side_effects`). `blast.preview()` and the run state carry `blast` and `blast_caption` per step. `tests/test_blast_targets.py` fails on a target kind the engine accepts with no entry, or a part the Circuit view does not draw.

**Step words.** `describe()` in `testrunmodel.js` gives every step one sentence for the rail, the strip, the badge labels and the Test run tab. A new step key in the engine fails `tests/test_step_words.py` until it has a sample there and words in `describe()`; a shipped step that falls back to JSON fails it too (`RAW_OK` only shrinks). The same test covers expectations (`expectWords()`; the engine's label stays the key that joins plan and result; `EXPECT_RAW_OK` only shrinks) and the part ids the engine names (`partWords()`, `PART_WORDS` in `runlayermodel.js`).

```bash
node --test webui/tests/js/runlayermodel.test.mjs        # grouping, one badge per part, glyphs and words, Tab order, pins, the strip's words
.venv/bin/python -m pytest tests/test_scenario_touches.py  # every shipped step lands on a part, or is listed with its reason
.venv/bin/python -m pytest tests/test_step_words.py       # every shipped step and every engine step key reads as a sentence, not JSON
.venv/bin/python -m pytest tests/test_blast_targets.py    # a declared blast entry for every target kind, parts the Circuit view draws
.venv/bin/python scripts/ui_shots.py                       # a sample run, no cluster: badges, order, rail, strip, phone sheet, reduced motion
```

## Traffic analysis

`trafficanalysis.js` (pure, `node --test`) computes rates, river bands, probe readings, periods and findings from `/api/traffic`; `analysis.js` draws them and
polls only while the Traffic tab is visible. To add a reading: add the pure function and its test first, return `null` where the record cannot say, and draw `null`
as "not measured", never 0. Periods are found per stream. `ui_shots.py` feeds the screen synthetic `/api/traffic` data and checks the river, the findings, the hash
round trip and that nothing polls while another tab is open.

## Under the hood

<p align="center"><img src="../assets/art/guide-web-ui-canvas-diagram.svg" alt="Colour tokens, a wire drawn as halo, core and pulse, and the three zoom levels Fleet, Cluster and Pool" width="720"></p>

> [!HOOD]
> Colours come from `docs/assets/palette.yaml`, generated into `webui/static/tokens.css` (see [the colour system](colour-system.md); each plane has one colour and one line type), pinned by `webui/tests/js/tokens.test.mjs`. A wire is a halo, a core and a moving pulse, one colour per plane (`wire.js`). The page's zoom has three levels, Fleet, Cluster and Pool.
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
