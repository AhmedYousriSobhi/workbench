"""Screenshot and sanity-check the Web UI in headless Chromium (needs: pip install -e '.[browser]' && playwright install chromium).

    .venv/bin/python scripts/ui_shots.py [OUTDIR]     # default /tmp/workbench-ui-shots

Starts `python -m webui` on a free port, captures every tab at desktop and phone width in light and dark, and reports
console errors and horizontal overflow. Prints the PNG paths; exit 1 if any check fails.
"""
import contextlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from benchctl import scenario as _scn
from webui import blast, graph
from webui.app import login_url

SYNTH_CLUSTER = """lastCluster = JSON.stringify({logins: [{name: 'login', mode: 'both', port: 2222}, {name: 'login2', mode: 'teleport', port: null}], services: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'jira-real': true, 'gpu-exporter': true, 'dcgm-exporter': true, 'real-gpu': true, 'teleport': true, 'jump': true, 'controller': true}, actions: [{id: 'power-off', label: 'Power off', gpu_only: false}, {id: 'power-on', label: 'Power on', gpu_only: false}, {id: 'overheat', label: 'Overheat', gpu_only: false}, {id: 'fan-fail', label: 'Fan failure', gpu_only: false}, {id: 'clear', label: 'Clear faults', gpu_only: false}],
                            nodes: ['cn001', 'cn002', 'cn003', 'cn004'].map(n => ({name: n, kind: 'compute', state: 'ok', label: 'Healthy', temp: 45, power: 'On', gpus: 0}))
                              .concat(['gpu001', 'gpu002'].map(n => ({name: n, kind: 'gpu', state: n === 'gpu002' ? 'fault' : 'ok', label: n === 'gpu002' ? 'GPU error' : 'Healthy', temp: 44, power: 'On', gpus: 8, gpus_busy: 4}))).concat([{name: 'rgpu001', kind: 'real-gpu', state: 'ok', label: 'Healthy', temp: 55, power: 'On', gpus: 1, gpus_busy: 1}])}); renderCluster(JSON.parse(lastCluster)); clearTimeout(clusterTimer); if (flowEs) flowEs.close(); flowSnap = {reachable: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'jira-real': true, 'gpu-exporter': true, 'dcgm-exporter': true, 'real-gpu': true, teleport: true}, totals: {'bmc-farm': 42, 'bcm-mock': 17, 'jira-mock': 9, 'jira-real': 0, 'gpu-exporter': 120, teleport: 5}, faults: 0}; recent = [{t: Date.now(), to: 'jira-mock', n: 4, fault: true}, {t: Date.now(), to: 'jira-real', n: 0, fault: true}, {t: Date.now(), to: 'bmc-farm', n: 6, fault: false}]; paintBlocks();"""  # six healthy nodes; stops the poll so the real (stopped) stack does not overwrite them
SYNTH_GRAPH = graph.build(
    {k: True for k in ("bmc-farm", "bcm-mock", "jira-mock", "jira-real", "gpu-exporter", "dcgm-exporter", "real-gpu", "teleport", "jump", "controller")},
    [{"name": n, "kind": "compute"} for n in ("cn001", "cn002", "cn003", "cn004")] + [{"name": n, "kind": "gpu"} for n in ("gpu001", "gpu002")],
    [{"name": "login", "mode": "both"}, {"name": "login2", "mode": "teleport"}])
SYNTH_CLUSTER = SYNTH_CLUSTER.replace("actions: [", "graph: " + json.dumps(SYNTH_GRAPH) + ", blast: " + json.dumps(blast.payload()) + ", actions: [", 1)
TEST_HOOK = "window.__WB_TEST = true; try { sessionStorage.setItem('workbench-circuit', '0'); sessionStorage.getItem('workbench-tab') || sessionStorage.setItem('workbench-tab', 'nodes'); } catch (e) {}"  # (the classic diagram: the circuit is the default and has its own checks) main.js then exposes its modules as globals (webui/static/js/testhook.js) so the checks below can call them by name
VIEWPORTS = {"desktop": (1280, 900), "phone": (390, 800)}


def sample_run(applied: int = 3, name: str = "node-down") -> dict:
    """A run state as GET /api/scenarios/run reports it (ADR 0086): `name` with its first `applied` steps done. Sample data: no
    scenario runs, the page's poll is answered by the test."""
    import yaml
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    pv = blast.preview(_scn.load(name, ROOT / "scenarios", sim), sim)  # with each step's declared blast, as the page's endpoint answers
    return {"running": True, "name": name, "speed": 1.0, "stopped": False, "ok": None, "trace": None, "started": 1763200000.0, "elapsed": 7.0,
            "plan_expects": [{k: x[k] for k in ("e", "label", "ids", "page")} for x in pv["expects"]], "expects": [],
            "steps": [{**{k: x[k] for k in ("at", "target", "do", "n", "ids", "no_part", "page", "blast", "blast_caption")}, "status": "ok" if i < applied else "pending", "detail": "",
                       "t": x["at"] if i < applied else None} for i, x in enumerate(pv["steps"])]}


WIRE_PROBE = """async () => {
  const w = await import('/static/js/wire.js');
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.id = 'wirecheck'; svg.setAttribute('width', '100'); svg.setAttribute('height', '16');
  svg.append(w.buildWire({ d: 'M0 8 L90 8', plane: 'access', rpm: 12, errors: 2, label: 'probe' }), w.buildWire({ d: 'M0 8 L90 8', plane: 'data' }));
  document.body.append(svg);
  const look = sel => { const e = svg.querySelector(sel); if (!e) return null; const c = getComputedStyle(e); return { name: c.animationName, state: c.animationPlayState, dash: c.strokeDasharray, dur: c.animationDuration }; };
  return { pulse: look('.wire-pulse'), err: look('.wire-err'), idle: look('.wire-idle'), title: svg.querySelector('title').textContent, state: svg.querySelector('.wire').dataset.state,
           layers: svg.querySelectorAll('.wire')[0].children.length, idleLayers: svg.querySelectorAll('.wire')[1].children.length };
}"""
WIRE_PAUSE = """async () => {
  const pulse = () => getComputedStyle(document.querySelector('#wirecheck .wire-pulse')).animationPlayState;
  const before = pulse();
  Object.defineProperty(document, 'hidden', { get: () => true, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
  const hidden = { state: pulse(), flag: document.documentElement.hasAttribute('data-tab-hidden') };
  delete document.hidden;
  document.dispatchEvent(new Event('visibilitychange'));
  return { before, hidden, after: pulse(), flagAfter: document.documentElement.hasAttribute('data-tab-hidden') };
}"""


def check_wire(page) -> list[str]:
    """The shared wire (webui/static/js/wire.js, canvas.css): four strokes and a red dash, a text alternative, motion that stops
    under reduced motion and while the tab is hidden, and the self-hosted fonts that really load. Returns the problems found."""
    bad = []
    r = page.evaluate(WIRE_PROBE)
    if r["layers"] != 6 or r["idleLayers"] != 3:  # title + 4 strokes + error dash; title + base + idle stroke
        bad.append(f"unexpected layers {r['layers']}/{r['idleLayers']}")
    if r["state"] != "errors" or "probe: Access wire, 12 requests a minute, 2 errors" != r["title"]:
        bad.append(f"no text alternative: {r['title']!r} / {r['state']}")
    if not page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"):
        for k in ("pulse", "err"):
            if r[k]["name"] != "wire-flow" or r[k]["state"] != "running":
                bad.append(f"{k} does not animate: {r[k]}")
        if r["pulse"]["dash"].replace("px", "").replace(",", "") != "2 18" or r["err"]["dash"].replace("px", "").replace(",", "") != "1 60":
            bad.append(f"dash patterns changed: {r['pulse']['dash']} / {r['err']['dash']}")
        p = page.evaluate(WIRE_PAUSE)
        if p["before"] != "running" or p["hidden"] != {"state": "paused", "flag": True} or p["after"] != "running" or p["flagAfter"]:
            bad.append(f"hidden tab does not pause the wires: {p}")
        page.emulate_media(reduced_motion="reduce")
        names = page.evaluate("[...document.querySelectorAll('#wirecheck .wire-pulse, #wirecheck .wire-err')].map(e => getComputedStyle(e).animationName)")
        page.emulate_media(reduced_motion="no-preference")
        if names != ["none", "none"]:
            bad.append(f"reduced motion leaves animation on: {names}")
    if r["idle"]["name"] != "none":
        bad.append("an idle wire animates")
    fonts = page.evaluate("Promise.all([document.fonts.load('600 16px Inter'), document.fonts.load('400 13px \"JetBrains Mono\"')]).then(f => f.map(x => x.length))")
    if fonts != [1, 1]:
        bad.append(f"fonts did not load from /static/fonts: {fonts}")
    page.evaluate("document.getElementById('wirecheck').remove()")
    return bad


def check_circuit(page) -> list[str]:
    """The Cluster level (webui/static/js/circuit.js): the same parts and wires whatever the cluster has, absent parts greyed, one
    layer isolated at a time, a text alternative on every wire. Leaves the page on the view it found. Returns the problems found."""
    bad = []
    page.click("#vw-cv")
    page.wait_for_selector("#flow[data-cv] .cv-box")
    r = page.evaluate("""() => { const q = s => [...document.querySelectorAll(s)], wires = q('#flow g.wire');
        return { wires: wires.length, untitled: wires.filter(w => !w.querySelector('title')?.textContent).length, parts: q('#flow [data-part]').length, layers: q('#cvstack button[data-layer]').length,
                 pressed: q('#cvstack button[aria-pressed=true]').length, bands: q('#flow .cv-band').length,
                 greyed: q('#flow [data-part][data-present=false]').every(e => +getComputedStyle(e).opacity <= 0.1), noteOnAbsent: q('#flow .cv-band.absent').every(b => b.querySelector('.cv-note')) } }""")
    if r["wires"] < 25 or r["untitled"] or r["parts"] < 20 or r["layers"] != 6 or r["pressed"] != 1 or r["bands"] != 2:
        bad.append(f"unexpected circuit: {r}")
    if not r["greyed"] or not r["noteOnAbsent"]:
        bad.append(f"an absent part is not greyed with a note: {r}")
    page.click("#cvstack button[data-layer=access]")
    page.wait_for_timeout(500)  # wires fade over 0.35 s
    f = page.evaluate("""() => { const w = [...document.querySelectorAll('#flow g.wire')]; return { faded: w.filter(x => x.dataset.faded === '1').length, lit: w.filter(x => x.dataset.faded === '0' && x.dataset.layer !== 'access').length,
        opacity: w.filter(x => x.dataset.faded === '1').every(x => +getComputedStyle(x).opacity <= 0.11) } }""")
    if not f["faded"] or f["lit"] or not f["opacity"]:
        bad.append(f"the Access layer does not isolate its wires: {f}")
    page.click("#cvstack button[data-layer=all]")
    bad += check_zoom(page)
    page.click("#vw-iso")
    if page.evaluate("document.getElementById('flow').dataset.cv") is not None:
        bad.append("leaving the circuit left it on")
    return bad


def check_zoom(page) -> list[str]:
    """The zoom (webui/static/js/zoom.js): Cluster to Pool to Fleet and back. After each move exactly one layer is shown and the others
    are out of layout and inert, so a level never stays visible or clickable behind the one that replaced it. Returns the problems found."""
    bad = []
    probe = """() => Object.fromEntries([['fleet', '#fleetview'], ['cluster', '#trafficpanel > .flowscroll'], ['pool', '#poolview']].map(([k, s]) => {
        const e = document.querySelector(s); return [k, e.hidden || getComputedStyle(e).display === 'none' ? (e.inert ? 'gone' : 'hidden but not inert') : 'shown']; }))"""

    def only(level, step):
        page.wait_for_timeout(1100)  # the move takes 0.7 s
        got = page.evaluate(probe)
        if any((v == "shown") != (k == level) or (v != "shown" and v != "gone") for k, v in got.items()):
            bad.append(f"after {step} only the {level} layer should show: {got}")

    if not page.locator("#flow .cv-rack .hit").count():
        return bad  # a cluster with no nodes has no pool to open
    page.locator("#flow .cv-rack .hit").first.dispatch_event("click")
    only("pool", "opening a rack")
    if not page.locator(".pv-tile").count() or not page.locator(".pv-filters button").count():
        bad.append("the pool level drew no tiles or filters")
    page.keyboard.press("Escape")
    only("cluster", "Escape in the pool")
    if page.evaluate("innerWidth <= 640"):  # E2 (#861): a phone has the current level with - and + only
        page.click("#zr-out")
    else:
        page.click("#zoomrail [data-level=fleet]")
    only("fleet", "the FLEET stop")
    page.click(".fl-card .fl-head")
    only("cluster", "opening a card")
    return bad


RUN_PROBE = """() => { const q = s => [...document.querySelectorAll(s)], badges = q('#runlayer .rl-badge');
  return { badges: badges.map(b => ({ n: +b.dataset.n, label: b.getAttribute('aria-label') || '', text: b.textContent, role: b.getAttribute('role'), tab: b.getAttribute('tabindex') })),
           pins: q('#runlayer .rl-pin').map(p => p.getAttribute('aria-label') || ''), rail: q('#runrail li').map(l => l.textContent.replace(/\\s+/g, ' ').trim()),
           ring: q('#flow [data-runring]').length, head: document.getElementById('run-head').textContent, sheet: !document.getElementById('runsheet').hidden,
           sheetText: document.getElementById('rs-title').textContent, moving: document.getAnimations().filter(a => a.effect && a.effect.target && a.effect.target.closest && a.effect.target.closest('#runlayer, #runsheet, #ipane-run')).length,
           wide: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 } }"""


# The badges' size on screen and whether any two overlap (issue 813): text >= 14 px, a tap target >= 40 px, no pill over another.
MARK_PROBE = """() => { const f = document.getElementById('flow'), k = f.getScreenCTM().a, marks = [...document.querySelectorAll('#runlayer [role=button], #runlayer .rl-blast')];
  const box = el => el.getBoundingClientRect(), pills = marks.map(m => box(m.querySelector('.rl-pill'))), out = { n: marks.length, small: [], tiny: [], overlap: [] };
  marks.forEach((m, i) => { const fs = parseFloat(getComputedStyle(m.querySelector('.rl-t')).fontSize) * k, hit = box(m);
    if (fs < 14) out.small.push(`${m.dataset.key}: ${fs.toFixed(1)} px`);
    if (m.getAttribute('role') === 'button' && (hit.width < 40 || hit.height < 40)) out.tiny.push(`${m.dataset.key}: ${Math.round(hit.width)}x${Math.round(hit.height)}`);
    for (let j = 0; j < i; j++) { const a = pills[i], b = pills[j];
      if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) out.overlap.push(`${m.dataset.key} / ${marks[j].dataset.key}`); } });
  return out; }"""


def mark_problems(page, what: str) -> list[str]:
    m = page.evaluate(MARK_PROBE)
    return ([f"{what}: badge text under 14 px on screen: {m['small'][:3]}"] if m["small"] else []) + \
        ([f"{what}: badge tap targets under 40 px: {m['tiny'][:3]}"] if m["tiny"] else []) + \
        ([f"{what}: badges overlap: {m['overlap'][:3]}"] if m["overlap"] else [])


PREVIEW_PROBE = """() => { const q = s => [...document.querySelectorAll(s)];
  return { layer: !!document.querySelector('#runlayer.preview'), badges: q('#runlayer .rl-badge').map(b => ({ id: b.dataset.id, ghost: b.classList.contains('ghost'), label: b.getAttribute('aria-label') || '', text: b.textContent,
             box: (r => [Math.round(+r.getAttribute('x')), Math.round(+r.getAttribute('y'))])(b.querySelector('.rl-pill')) })),  // in drawing units: a scroll is not a jump
           pins: q('#runlayer .rl-pin').map(p => p.querySelector('.rl-t').textContent + '|' + (p.getAttribute('aria-label') || '')), blast: q('#runlayer .rl-blast').map(m => (m.getAttribute('role') || '') + '|' + (m.getAttribute('aria-label') || '')),
           ghosts: q('#flow [data-runghost]').length, head: document.getElementById('run-head').textContent, sum: document.getElementById('run-sum').textContent,
           blastRail: q('#runrail li').filter(l => /Clients may see a change on/.test(l.textContent)).length, topo: !document.getElementById('tab-nodes').hidden } }"""


def check_preview(page, shot: str, state: dict) -> list[str]:
    """ADR 0086 phase 2, before Play: the scenario picked in Test run drawn as ghosts (dashed badges that say planned, a dotted ring on every
    part it will touch, the E1 pin with ↷, ◎ where clients may see a change), reached with Show on the diagram; Play turns the ghosts into
    live badges on the same parts without moving them; Stop leaves the plain diagram. Sample data: the preview is the page's own endpoint."""
    bad = []
    page.evaluate("async () => { trPrev = await (await fetch('/api/scenarios/node-down')).json(); runHidden = ''; trLast = {running: false}; }")
    page.click("#tb-run")
    page.evaluate("paintPreview(trLast)")
    if not page.locator("#run-show").is_visible() or page.locator("#run-watch").is_visible():
        bad.append("Test run offers no Show on the diagram before the scenario has run (or offers Watch instead)")
    page.click("#run-show")
    page.wait_for_selector("#runlayer.preview .rl-badge", timeout=5000)
    r = page.evaluate(PREVIEW_PROBE)
    if not r["topo"]:
        bad.append("Show on the diagram did not open the Topology tab")
    if len(r["badges"]) < 3 or not all(b["ghost"] and ", planned: " in b["label"] and "○" in b["text"] for b in r["badges"]):
        bad.append(f"the preview badges are not ghosts that say planned: {r['badges'][:2]}")
    if r["ghosts"] < 3:
        bad.append(f"the parts the scenario will touch have no dotted ring: {r['ghosts']}")
    if not r["pins"] or not r["pins"][0].startswith("E1 ↷|Expectation E1, CLI only"):
        bad.append(f"the preview's expectation pin is missing or not marked CLI only: {r['pins']}")
    if not r["blast"] or not all(m.startswith("img|Clients of this part may see a change") for m in r["blast"]):
        bad.append(f"no ◎ mark with words where clients may see a change: {r['blast']}")
    if "Preview: nothing has run yet" not in r["head"] or "touch" not in r["sum"] or not r["blastRail"]:
        bad.append(f"the Run tab does not say it is a preview, what it touches and whose clients may see a change: {r['head']!r} / {r['sum']!r}")
    bad += mark_problems(page, "preview")
    page.screenshot(path=shot.replace(".png", "-preview.png"), full_page=False)
    ghost = {b["id"]: b["box"] for b in r["badges"]}
    page.route("**/api/scenarios/run", lambda rt: rt.fulfill(json=state))  # Play: the run state of the same scenario takes over
    page.evaluate("poll()")
    page.wait_for_selector("#runlayer:not(.preview) .rl-badge", timeout=5000)
    live = page.evaluate(PREVIEW_PROBE)
    if [b["id"] for b in live["badges"]] != list(ghost) or any(b["ghost"] for b in live["badges"]) or live["ghosts"]:
        bad.append(f"Play did not turn the ghosts into live badges on the same parts: {[b['id'] for b in live['badges']]} vs {list(ghost)}")
    moved = [b["id"] for b in live["badges"] if abs(b["box"][0] - ghost[b["id"]][0]) > 8 or b["box"][1] != ghost[b["id"]][1]]
    if moved:
        bad.append(f"badges jumped when the run took over: {moved}")
    stopped = {**state, "running": False, "stopped": True, "ok": True}
    page.unroute("**/api/scenarios/run")
    page.route("**/api/scenarios/run", lambda rt: rt.fulfill(json=stopped))
    page.evaluate("runStopHide = 'node-down'; poll()")  # Stop pressed on this page
    page.wait_for_function("!document.querySelector('#runlayer') && !document.querySelector('#flow [data-runring], #flow [data-runghost]')", timeout=5000)
    page.unroute("**/api/scenarios/run")
    return bad


REPLAY_PROBE = """() => { const sl = document.getElementById('rs-slider'), q = s => [...document.querySelectorAll(s)], sh = document.getElementById('runsheet').getBoundingClientRect();
  return { role: sl.getAttribute('role'), min: sl.getAttribute('aria-valuemin'), max: sl.getAttribute('aria-valuemax'), now: sl.getAttribute('aria-valuenow'),
           text: sl.getAttribute('aria-valuetext') || '', label: sl.getAttribute('aria-label') || '', tab: sl.getAttribute('tabindex'), active: document.activeElement.id,
           shown: !document.getElementById('rs-replay').hidden && !document.getElementById('runsheet').hidden, cap: document.getElementById('rs-rcap').textContent,
           play: !document.getElementById('rs-play').hidden, sel: (document.querySelector('#runrail [aria-pressed=true]') || { dataset: {} }).dataset.n,
           nowBadges: q('#runlayer .rl-badge').filter(b => (b.getAttribute('aria-label') || '').includes(', now: ')).map(b => +b.dataset.n),
           badges: q('#runlayer .rl-badge').length, ring: q('#flow [data-runring]').length, head: document.getElementById('run-head').textContent,
           h: sl.getBoundingClientRect().height, top: sh.top, bottom: sh.bottom, fixed: getComputedStyle(document.getElementById('runsheet')).position === 'fixed',
           last: !document.getElementById('run-last').hidden, again: !document.getElementById('run-replay').hidden && document.getElementById('run-replay').textContent,
           moving: document.getAnimations().filter(a => a.effect && a.effect.target && a.effect.target.closest && a.effect.target.closest('#runlayer, #runsheet')).length,
           wide: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 } }"""


def check_replay(page, shot: str, vp: str) -> list[str]:
    """ADR 0086 phase 3, after the run: Stop leaves the plain diagram and the Run tab offers Replay the last run; the strip becomes a
    WAI-ARIA slider over the recorded steps only (role, value, aria-valuetext naming the step), driven end to end by the keyboard; it moves
    the current-step badge, the ring and the rail selection; the strip fits a phone; no playback and nothing moving under reduced motion."""
    bad = []
    stopped = sample_run(3)
    stopped.update(running=False, stopped=True, ok=True, elapsed=9.0)
    page.unroute("**/api/scenarios/run")
    page.route("**/api/scenarios/run", lambda r: r.fulfill(json=stopped))
    page.evaluate("trPrev = null; runHidden = ''; runStopHide = 'node-down'; poll()")  # Stop pressed on this page: the plain diagram comes back
    page.wait_for_function("trLast && trLast.stopped", timeout=5000)
    page.evaluate("showPane('run'); paintRun()")
    r = page.evaluate(REPLAY_PROBE)
    if r["badges"] or not r["last"]:
        bad.append(f"after Stop the diagram is not plain, or the Run tab offers no Replay the last run: {r['badges']} badges, offered {r['last']}")
    page.focus("#run-lastbtn")
    page.keyboard.press("Enter")
    r = page.evaluate(REPLAY_PROBE)
    if (r["role"], r["min"], r["max"], r["now"], r["tab"]) != ("slider", "1", "3", "1", "0") or not r["label"]:
        bad.append(f"the replay slider's ARIA attributes are wrong (3 recorded steps): {r}")
    if r["active"] != "rs-slider" or not r["shown"]:
        bad.append(f"Replay the last run did not show the strip and focus the slider: {r['active']}")
    if not r["text"].startswith("Step 1, 1 of 3 recorded: ") or "never ran" not in r["cap"] or "stay live" not in r["cap"]:
        bad.append(f"aria-valuetext does not name the step, or the strip does not say it walks recorded steps only: {r['text']!r} / {r['cap']!r}")
    for key, want in (("End", "3"), ("Home", "1"), ("ArrowRight", "2")):
        page.keyboard.press(key)
        r = page.evaluate(REPLAY_PROBE)
        if r["now"] != want or r["sel"] != want or set(r["nowBadges"]) != {int(want)} or not r["ring"] or f"Replay: step {want}" not in r["head"]:
            bad.append(f"{key} did not move the slider, the rail selection, the current badge and the ring to step {want}: {r}")
    page.keyboard.press("Tab")
    if page.evaluate("document.activeElement.id") != "rs-end":
        bad.append("Tab from the slider does not reach End replay")
    page.keyboard.press("Shift+Tab")
    r = page.evaluate(REPLAY_PROBE)
    if r["h"] < 40 or r["wide"]:
        bad.append(f"the slider is under 40 px tall or the page scrolls sideways: {r['h']}")
    if vp == "phone" and (not r["fixed"] or r["bottom"] > page.viewport_size["height"] + 1 or r["top"] < page.viewport_size["height"] * 0.4):
        bad.append(f"on a phone the replay strip is not a bottom sheet that leaves the diagram room: {r['top']}..{r['bottom']}")
    small = page.evaluate("[...document.querySelectorAll('#rs-replay button')].filter(b => !b.hidden && b.getBoundingClientRect().height < 40).map(b => b.id)")
    if small:
        bad.append(f"replay buttons under 40 px: {small}")
    page.screenshot(path=shot.replace(".png", "-replay.png"), full_page=False)
    page.emulate_media(reduced_motion="reduce")
    page.keyboard.press("ArrowRight")
    r = page.evaluate(REPLAY_PROBE)
    if r["play"] or r["moving"] or r["now"] != "3":
        bad.append(f"under reduced motion the replay offers playback, animates, or does not change in place: play {r['play']}, moving {r['moving']}, at {r['now']}")
    page.emulate_media(reduced_motion="no-preference")
    page.focus("#rs-end")
    page.keyboard.press("Enter")
    r = page.evaluate(REPLAY_PROBE)
    if r["shown"] or "Replay" in r["head"] or r["again"] != "Replay the run":
        bad.append(f"End replay did not return to the run as it ended, with Replay the run offered: {r['head']!r} / {r['again']!r}")
    return bad


def check_cli_run(page, shot: str) -> list[str]:
    """ADR 0086 phase 4: a run from the CLI (`benchctl scenario run` posted it to the page) is drawn like a page run and said in words, in
    the Run tab's heading, the strip over the diagram and the layer's label; its CLI-only steps show their real result, never ↷ CLI only."""
    bad, name = [], "slurm-upgrade-mid-job"
    run = sample_run(99, name)
    run.update(running=False, ok=True, source="cli", elapsed=14.0, expects=[{**x, "status": "ok", "detail": "", "t": 10.0} for x in run["plan_expects"]])
    page.unroute("**/api/scenarios/run")
    page.route("**/api/scenarios/run", lambda r: r.fulfill(json=run))
    page.evaluate("trPrev = null; runHidden = ''; runSel = null; replay = null; poll()")
    page.wait_for_function(f"trLast && trLast.name === '{name}'", timeout=5000)
    page.evaluate("watchRun()")
    page.wait_for_selector("#runlayer .rl-badge", timeout=5000)
    r = page.evaluate(RUN_PROBE)
    extra = page.evaluate("({ layer: document.getElementById('runlayer').getAttribute('aria-label'), line: document.getElementById('rs-line').textContent, "
                          "sum: document.getElementById('run-sum').hidden ? '' : document.getElementById('run-sum').textContent })")
    if "run from the CLI" not in r["head"] or "run from the CLI" not in extra["layer"] or not extra["line"].startswith("Run from the CLI"):
        bad.append(f"a run from the CLI is not said in words: {r['head']!r} / {extra['line']!r}")
    if "Ctrl+C" in extra["sum"] or "ran on the host" not in extra["sum"]:
        bad.append(f"the Run tab does not say where a finished CLI run ran: {extra['sum']!r}")
    if not r["badges"] or any("↷" in b["text"] or "CLI only" in b["label"] for b in r["badges"]):
        bad.append(f"a CLI run's badges show ↷ CLI only instead of their real result: {[b['text'] for b in r['badges']]}")
    if any("CLI only" in p for p in r["pins"]) or not all(", held: " in p for p in r["pins"]):
        bad.append(f"a CLI run's expectation pins do not show their result: {r['pins']}")
    if r["wide"]:
        bad.append("a CLI run makes the page scroll sideways")
    bad += mark_problems(page, name)
    page.screenshot(path=shot.replace(".png", "-cli.png"), full_page=False)
    page.locator("#ipane-run").screenshot(path=shot.replace(".png", "-cli-rail.png"))  # the Run tab: heading and the line under it
    return bad


def check_runlayer(page, shot: str, vp: str) -> list[str]:
    """The run layer (ADR 0086, webui/static/js/runlayer.js) with a sample run, no cluster: numbered badges with a glyph and words on the
    parts each step touched, in step order for Tab; the step rail; the strip over the diagram (a bottom sheet on a phone) with Previous and
    Next; the blast radius still drawn; nothing moving under reduced motion. Leaves the page on the classic view with no run shown."""
    bad, state = [], sample_run()
    page.evaluate(SYNTH_CLUSTER)
    try:
        bad += check_preview(page, shot, sample_run(1))
        page.evaluate("runHidden = ''")
        page.route("**/api/scenarios/run", lambda r: r.fulfill(json=state))
        page.evaluate("poll()")
        page.wait_for_function("trLast && trLast.name === 'node-down'", timeout=5000)
        page.evaluate("watchRun()")
        page.wait_for_selector("#runlayer .rl-badge", timeout=5000)
        r = page.evaluate(RUN_PROBE)
        ns = [b["n"] for b in r["badges"]]
        if len(r["badges"]) < 3 or ns != sorted(ns):
            bad.append(f"badges missing or not in step order (Tab must follow the scenario): {ns}")
        for b in r["badges"]:
            if not b["label"].startswith("Step ") or b["role"] != "button" or b["tab"] != "0" or not any(g in b["text"] for g in "✓●○↷✗◐"):
                bad.append(f"a badge without a number, glyph and spoken words: {b}")
        if not any(", now: " in b["label"] for b in r["badges"]) or not any(", done: " in b["label"] for b in r["badges"]):
            bad.append("the badges do not say done and now in words")
        if len(r["rail"]) != 6 or "Step 3: now" not in " ".join(r["rail"]) or "Step 4: next" not in " ".join(r["rail"]):
            bad.append(f"the step rail does not list every step with its state in words: {r['rail'][:3]}")
        if not r["pins"] or not r["pins"][0].startswith("Expectation E1, CLI only"):
            bad.append(f"the expectation pin is missing or says nothing: {r['pins']}")
        if r["ring"] < 2 or "step 3 of 6" not in r["head"]:
            bad.append(f"the current step's parts are not ringed, or the heading is wrong: {r['ring']} / {r['head']!r}")
        if not r["sheet"] or not r["sheetText"].startswith("Step 3:"):
            bad.append(f"the step strip over the diagram is missing: {r['sheetText']!r}")
        if r["wide"]:
            bad.append("the run layer makes the page scroll sideways")
        bad += mark_problems(page, "node-down")
        box = page.locator("#runsheet").bounding_box()
        if vp == "phone" and (box is None or box["y"] + box["height"] > page.viewport_size["height"] + 1 or page.evaluate("getComputedStyle(document.getElementById('runsheet')).position") != "fixed"):
            bad.append(f"on a phone the step sheet is not fixed to the bottom of the screen: {box}")
        small = page.evaluate("[...document.querySelectorAll('#runsheet button')].filter(b => b.getClientRects().length && b.getBoundingClientRect().height < 40).map(b => b.id)")
        if small:
            bad.append(f"step sheet buttons under 40 px: {small}")
        page.click("#rs-next")
        if not page.locator("#rs-title").text_content().startswith("Step 4:") or page.get_attribute("#runrail [data-n='4']", "aria-pressed") != "true":
            bad.append("Next did not move the strip and the rail to step 4")
        page.locator("#runlayer .rl-badge").first.dispatch_event("click")
        if page.get_attribute("#runrail [data-n='1']", "aria-pressed") != "true":
            bad.append("a badge did not select its step in the rail")
        page.evaluate("setBlast(blastFor({to: 'bmc-farm', label: 'power-off on cn002', nodes: ['cn002']}))")
        dash = page.evaluate("(() => { const b = document.querySelector('#flow [data-blast] > .cv-box'); return b ? getComputedStyle(b).strokeDasharray : null; })()")
        if not dash or dash == "none":
            bad.append(f"the blast radius lost its dashes under the run layer: {dash}")
        page.screenshot(path=shot, full_page=False)
        page.evaluate("setBlast(null)")
        page.emulate_media(reduced_motion="reduce")
        page.evaluate("paintRunLayer(true)")
        if page.evaluate(RUN_PROBE)["moving"]:
            bad.append("something in the run layer animates under reduced motion")
        page.emulate_media(reduced_motion="no-preference")
        dense = sample_run(99, "client-stress")  # a dense pool: every node of both racks has a badge, several steps per part
        page.unroute("**/api/scenarios/run")
        page.route("**/api/scenarios/run", lambda r: r.fulfill(json=dense))
        page.evaluate("poll()")
        page.wait_for_function("trLast && trLast.name === 'client-stress'", timeout=5000)
        page.evaluate("paintRun(); paintRunLayer(true)")
        if page.evaluate("document.querySelectorAll('#runlayer .rl-badge').length") < 6:
            bad.append("client-stress: fewer than 6 badges on a run that touches every node")
        bad += mark_problems(page, "client-stress")
        page.screenshot(path=shot.replace(".png", "-dense.png"), full_page=False)
        bad += check_cli_run(page, shot)
        bad += check_replay(page, shot, vp)
    except PlaywrightError as e:
        bad.append(f"the run layer did not draw a sample run: {str(e).splitlines()[0]}")
    finally:
        page.unroute("**/api/scenarios/run")
        page.evaluate("trLast = {running: false}; trPrev = null; runHidden = ''; runStopHide = ''; runSel = null; replay = null; paintRun(); setCircuit(false); lastCluster = ''; loadCluster()")  # back to the page's own cluster answer
    return bad


_POLL_CHECKED = False


def check_shell(page) -> list[str]:
    """Shell (issue 861 B2, B4, B5, B6, E2): the zoom rail only on Topology and Traffic, the left tiles only on Overview, an all-clear
    tick chip instead of a band, one selected workspace, SVG icons in the navigation, one row of zoom rail on a phone."""
    bad = []
    narrow = page.evaluate("innerWidth <= 640")
    page.click("#tb-nodes")
    page.click("#vw-cv")  # the rail belongs to the circuit view; the classic diagram has no zoom
    for tab, rail, tiles in (("home", False, True), ("run", False, False), ("nodes", True, False), ("traffic", True, False)):
        page.click(f"#tb-{tab}")
        page.wait_for_timeout(300)
        if page.locator("#zoomrail").is_visible() != rail:
            bad.append(f"{tab}: the zoom rail must be {'shown' if rail else 'hidden'}")
        if page.locator("#kpis .kpi").count() and page.locator("#kpis").is_visible() != tiles:
            bad.append(f"{tab}: the left tiles must be {'shown' if tiles else 'hidden'} (Overview only)")
        if page.locator('[role=tab][aria-selected="true"]').count() != 1:
            bad.append(f"{tab}: exactly one workspace must be selected")
        clear = page.locator("#attn .ic.good").count() > 0
        if page.locator("#c-attn").is_visible() != clear or (tab in ("nodes", "traffic") and page.locator(".subbar").is_visible() == clear):
            bad.append(f"{tab}: all clear must show as the strip chip and no band; something to attend to as the band and no chip")
        if rail:
            rows = page.evaluate("(() => { const r = document.getElementById('zoomrail'); return [r.getBoundingClientRect().height, [...r.querySelectorAll('[data-level]')].filter(b => b.offsetParent).length]; })()")
            if narrow and (rows[0] > 60 or rows[1] > 1):
                bad.append(f"{tab}: on a phone the zoom rail is one row with the current level only (height {rows[0]:.0f}, {rows[1]} levels)")
    if page.locator(".side .sidei .ic, .menuitem .ic").count() < 8 or page.locator(".side .sidei, .menuitem").evaluate_all("els => els.some(e => /[\u2302\u25b6\u25a6\u21c4\u2316\u2699\u271a\u21e9\u270e]/.test(e.textContent))"):
        bad.append("navigation and menu icons must be SVG, not font glyphs")
    page.click("#tb-nodes")
    page.click("#vw-iso")  # back to the view check_circuit expects to find
    return bad


def check_run(page, shot: str) -> list[str]:
    """Test run (webui/static/js/testrun.js): the library, a chosen scenario's timeline before Play with the CLI-only steps marked in
    words (U7), the Client's eye panel, and no sideways scroll. Returns the problems found."""
    bad = []
    page.click("#tb-run")
    page.wait_for_selector("#list .scnsel", state="visible")
    if page.locator("#tab-nodes").is_visible() or page.locator("#scndlg").count():
        bad.append("Test run should be the only workspace shown and the scenarios drawer should be gone")
    names = page.locator("#list .scnsel").evaluate_all("els => els.map(e => e.dataset.name)")
    for name in names:  # the preview says what is CLI-only before anything runs, and says it in words
        page.click(f'#list .scnsel[data-name="{name}"]')
        try:
            page.wait_for_function("n => document.querySelector('#runtitle').textContent.startsWith(n) && document.querySelectorAll('#timeline li').length > 0", arg=name, timeout=15000)
        except PlaywrightError:  # name the scenario: a bare timeout does not say which preview never drew
            bad.append(f"{name}: the preview drew no title or timeline within 15 s (title now: {page.locator('#runtitle').text_content()!r})")
            continue
        tags, notice = page.locator("#timeline .clitag").count(), page.locator("#runcli").is_visible()
        cli_exp = "(CLI only)" in page.locator("#expects").inner_text()
        if bool(tags or cli_exp) != notice:
            bad.append(f"{name}: the CLI-only notice and the CLI-only marks disagree")
        if tags and "CLI only" not in page.locator("#timeline .clitag").first.text_content():
            bad.append(f"{name}: a CLI-only mark without words")
        if page.locator("#run-export").is_visible():
            bad.append(f"{name}: export offered before any run")
    clipped = page.evaluate("""() => [...document.querySelectorAll('#list .scn')].filter(li => { const b = li.querySelector('.btn'), c = li.getBoundingClientRect(), r = b.getBoundingClientRect();
        return r.right > c.right + 1 || r.left < c.left - 1 || b.scrollWidth > b.clientWidth + 1 || r.width < 40; }).map(li => li.dataset.name)""")
    if clipped:  # D1 (#861): a long title must wrap, never push Play out of its card
        bad.append(f"Play button clipped or cut off on: {', '.join(clipped[:5])}")
    chips = page.locator("#scngroups [data-group]")  # D2: group chips with counts; D3: one sentence on the card
    total = int(page.locator('#scngroups [data-group="all"] .dim').inner_text())
    if total != page.locator("#list .scn").count() or sum(int(t) for t in page.locator('#scngroups [data-group]:not([data-group="all"]) .dim').all_inner_texts()) != total:
        bad.append("the group chips do not add up to the scenarios listed")
    if chips.count() > 2:
        chips.nth(1).click()
        shown = page.locator("#list .scn:visible").count()
        if shown != int(page.locator("#scngroups [data-group]").nth(1).locator(".dim").inner_text()) or page.locator("#list .scn:visible:not([data-group='" + page.locator("#scngroups [data-group]").nth(1).get_attribute("data-group") + "'])").count():
            bad.append("a group chip must show exactly its scenarios")
        page.click('#scngroups [data-group="all"]')
    if page.locator("#list .scnd").evaluate_all("els => els.some(e => /[.!?]\\s+\\S/.test(e.textContent.trim().replace(/\\b(e\\.g|i\\.e)\\./gi, '')))"):
        bad.append("a scenario card must show the first sentence only")
    if not page.locator("#run-eye").inner_text().strip():
        bad.append("Client's eye panel is empty")
    if page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"):
        wide = page.evaluate("[...document.querySelectorAll('#tab-run *')].filter(e => e.getBoundingClientRect().right > document.documentElement.clientWidth + 1).slice(0, 3).map(e => e.tagName + '#' + e.id + '.' + e.className)")
        bad.append(f"Test run scrolls sideways ({wide})")
    page.screenshot(path=shot)
    page.click("#tb-nodes")
    return bad


def check_hidden(page) -> list[str]:
    """W-11 (U1, U2): a hidden tab closes the governor feed and stops polling the cluster; showing it again resumes both."""
    bad = []
    page.evaluate("(() => { window.__reqs = 0; const f = window.fetch.bind(window); window.fetch = (...a) => { if (String(a[0]).includes('/api/cluster')) window.__reqs++; return f(...a); }; })()")  # an expression that ends in a function would be called by evaluate
    page.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(5200)
    if page.evaluate("window.__reqs") != 0:
        bad.append("the cluster was polled while the page was hidden")
    page.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(1500)
    page.evaluate("delete document.hidden")
    if page.evaluate("window.__reqs") < 1:
        bad.append("showing the page did not resume cluster polling at once")
    return bad


def check_home(page) -> list[str]:
    """Overview (webui/static/js/home.js): one card per node with its state in words, the three start cards, services with real or
    simulated, and a node card opens that node in Topology. Returns the problems found."""
    bad = []
    page.click("#tb-home")
    page.wait_for_selector("#home-nodes .hn", state="visible")
    if page.locator("#tab-home").is_hidden() or page.locator("#tab-nodes").is_visible():
        bad.append("Overview should be the only workspace shown")
    if page.locator("#home-nodes .hn").count() != page.evaluate("lastCl.nodes.length"):
        bad.append("Overview shows a different number of node cards than the snapshot has nodes")
    if page.locator("#home-nodes .hn .pill").evaluate_all("els => els.some(e => !e.textContent.trim().replace(/[^A-Za-z]/g, ''))"):
        bad.append("a node card shows its state without words")
    if page.locator(".home-start .hcard").count() != 3 or not page.locator("#home-svcs .hs .tag").count():
        bad.append("Overview lacks the start cards or the services row")
    if page.locator("#home-attn-list li").count() < 1:
        bad.append("Needs attention shows neither an item nor the all-clear line")
    if page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"):
        bad.append("Overview scrolls sideways")
    first = page.locator("#home-nodes .hn").first.get_attribute("data-open")
    page.locator("#home-nodes .hn").first.click()
    page.wait_for_selector("#nodepane", state="visible")
    if page.locator("#tab-home").is_visible() or first not in page.inner_text("#nodetitle"):
        bad.append("a node card did not open that node in Topology")
    page.click("#node-back")
    return bad


def check_analysis(page) -> list[str]:
    """The Traffic analysis screen (webui/static/js/analysis.js) with synthetic /api/traffic data: the river, the findings and the
    rhythm appear, a rate is not shown before two readings exist, a hash opens and clears the focused wire, a stale hash clears
    itself, and nothing polls while another tab is open (W-11). Leaves the page on the Traffic tab. Returns the problems found."""
    global _POLL_CHECKED
    bad, hits = [], {"n": 0}
    now = time.time()
    row = lambda who, svc, shape, n, se: {"who": who, "service": svc, "shape": shape, "n": n, "ok": n - se, "client_error": 0, "server_error": se, "ms": n * 4.0, "hist": [n, 0, 0, 0, 0, 0, 0, 0, 0]}
    recent = [[round(now - 300 + i * 5, 3), "unknown", "jira-mock", "GET /rest/api/2/serverInfo", 200, 3.0] for i in range(60)]
    recent += [[round(now - t, 3), "python-requests", "jira-mock", "GET /rest/api/2/search?jql", 503, 600.0] for t in (50, 120, 200)]
    body = {"now": round(now, 3), "edges": [5, 10, 25, 50, 100, 250, 500, 1000], "services": {"bmc-farm": True, "bcm-mock": True, "jira-mock": True},
            "streams": [row("unknown", "jira-mock", "GET /rest/api/2/serverInfo", 60, 0), row("python-requests", "jira-mock", "GET /rest/api/2/search?jql", 30, 3)], "recent": sorted(recent)}

    def answer(route):
        hits["n"] += 1
        route.fulfill(json=body)

    cap = {"starts": 0, "stops": 0, "reads": 0}

    def capture(route):
        url = route.request.url
        if url.endswith("/api/capture/start"):
            cap["starts"] += 1
            return route.fulfill(json={"ok": True, "node": "x", "seconds": 300})
        if url.endswith("/api/capture/stop"):
            cap["stops"] += 1
            return route.fulfill(json={"ok": True, "running": False})
        cap["reads"] += 1
        msgs = [{"t": now - 2, "dir": "in", "peer": "10.141.255.1", "peer_name": "slurmctld", "port": 6818, "size": 120, "type": "REQUEST_PING", "type_id": 1008, "kind": "request", "latency_ms": None, "version": 11520, "flags": 0, "body_length": 8},
                {"t": now - 1, "dir": "in", "peer": "10.141.0.9", "port": 6818, "size": 63, "type": "unknown", "type_id": None, "kind": "unknown", "latency_ms": None, "note": "the header does not parse"}]
        route.fulfill(json={"ok": True, "running": True, "node": "x", "messages": msgs if cap["reads"] == 1 else [], "next": 2, "ended": None, "error": None, "seconds_left": 290, "class": "P5"})

    page.route("**/api/capture/*", capture)
    page.route("**/api/traffic*", answer)
    page.evaluate("stopAnalysis(); startAnalysis()")
    try:
        page.wait_for_selector("#an-river .an-node", timeout=5000)
        page.wait_for_function("document.querySelectorAll('#an-found-list .an-f.no-retry, #an-found-list .an-f.timer').length >= 2", timeout=5000)
    except Exception:  # noqa: BLE001
        page.unroute("**/api/traffic*")
        page.unroute("**/api/capture/*")
        return ["the Traffic analysis screen did not draw the river and the findings from /api/traffic"]
    r = page.evaluate("""() => ({ status: document.getElementById('an-status').textContent, note: document.getElementById('an-river-note').textContent,
        nodes: document.querySelectorAll('#an-river .an-node').length, cols: document.querySelectorAll('.an-col').length, unk: document.querySelectorAll('.an-col.unk').length,
        rate: [...document.querySelectorAll('#an-river .an-ns')].some(n => /req\\/min/.test(n.textContent) && !/not measured/.test(n.textContent)) })""")
    if "recording" not in r["status"] or r["nodes"] != 9 or r["cols"] < 1 or r["unk"] < 1:
        bad.append(f"the analysis screen is incomplete: {r}")
    if r["rate"] or "Waiting for two readings" not in r["note"]:
        bad.append(f"a rate was shown before two readings existed: {r}")
    first = page.evaluate("clusterNow().nodes[0].name")
    page.evaluate(f"location.hash = {first!r}")
    page.wait_for_timeout(300)
    if not page.is_visible("#an-focus") or first not in page.inner_text("#an-focus"):
        bad.append("a node hash did not open the focused wire")
    try:
        page.wait_for_function("document.getElementById('an-capstate') && /Capturing/.test(document.getElementById('an-capstate').textContent) && document.querySelectorAll('#an-log tbody tr[data-i]').length >= 2", timeout=5000)
    except Exception:  # noqa: BLE001
        bad.append(f"the focused wire did not start a capture and show its messages: {page.inner_text('#an-capstate')!r}")
    if "unknown type" not in page.inner_text("#an-log") or page.locator(".an-cap").count() < 2 or "decoded" not in page.inner_text("#an-decoded").lower() or cap["starts"] != 1:
        bad.append(f"the capture panel is incomplete (an unknown type must say so in words; one capture started): starts={cap['starts']}")
    stops = cap["stops"]
    page.click("#an-clear")
    if cap["stops"] != stops + 1:
        bad.append("closing the focused wire did not stop the capture")
    if page.is_visible("#an-focus") or page.evaluate("location.hash") != "":
        bad.append("Clear focus did not clear the focus and the hash")
    page.evaluate("location.hash = 'no-such-node'")
    page.wait_for_timeout(200)
    if page.is_visible("#an-focus"):
        bad.append("a hash that names no node opened a focused wire")
    page.evaluate("history.replaceState(null, '', location.pathname + location.search)")
    if not _POLL_CHECKED:
        _POLL_CHECKED = True
        page.evaluate(f"location.hash = {first!r}")
        page.wait_for_function("document.getElementById('an-capstate') && /Capturing/.test(document.getElementById('an-capstate').textContent)", timeout=5000)
        stops = cap["stops"]
        page.click("#tb-nodes")
        before, reads = hits["n"], cap["reads"]
        page.wait_for_timeout(3500)  # a poll every 3 s (kept while visible, #538), a capture read every 2 s (stopped off the Traffic tab)
        if cap["reads"] != reads:
            bad.append("the wire capture kept reading while another tab was open (W-11)")
        if hits["n"] == before:
            bad.append("the traffic sampling stopped on another tab: it runs while the page is visible so a rate exists when Traffic opens (issue 538)")
        if cap["stops"] != stops + 1:
            bad.append("leaving the Traffic tab did not stop the capture")
        page.evaluate("history.replaceState(null, '', location.pathname + location.search)")
        page.click("#tb-traffic")
    page.unroute("**/api/traffic*")
    page.unroute("**/api/capture/*")
    page.evaluate("stopAnalysis(); startAnalysis()")
    return bad


STACK_DOWN = {"logins": [{"name": "login", "mode": "ssh", "port": 2222, "ip": "10.141.255.254"}, {"name": "login2", "mode": "teleport", "port": None, "ip": "10.141.255.252"}],
              "services": {k: False for k in ("bmc-farm", "bcm-mock", "jira-mock", "gpu-exporter", "teleport", "jump", "controller")}, "nodes": [], "actions": []}


def check_stack_down(browser, base_url: str, ui_token: str, vp: str, size: tuple[int, int], scheme: str, shot: str) -> list[str]:
    """With the stack down (SPEC W-10, issue 861 A1 and A2): one recovery command on the whole screen, and no tick in the diagram (a part
    that cannot answer shows one `offline` state, `built` is not a live state). Answered with sample data, so no cluster is touched."""
    bad = []
    for view in ("circuit", "classic"):
        pg = browser.new_page(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme)
        pg.add_init_script(TEST_HOOK.replace("sessionStorage.setItem('workbench-circuit', '0');", f"sessionStorage.setItem('workbench-circuit', '{int(view == 'circuit')}');"))
        pg.route("**/api/cluster", lambda r: r.fulfill(json=STACK_DOWN))
        pg.route("**/api/flow*", lambda r: r.abort())
        pg.goto(login_url(base_url, ui_token))
        pg.wait_for_function("typeof setTree === 'function'", timeout=15000)
        pg.wait_for_selector("#flow [data-part], #flow rect.blk", timeout=15000)
        pg.wait_for_timeout(800)
        said = pg.evaluate("document.body.innerText.split('benchctl start').length - 1")
        if said != 1:
            bad.append(f"stack down ({vp} {view} {scheme}): the recovery command shows {said} times on the screen, expected once")
        if pg.locator("#stackdown").count():
            bad.append("stack down: the #stackdown notice is back, the Needs attention entry is the one message")
        ticks = pg.evaluate("[...document.querySelectorAll('#flow text')].map(t => t.textContent).filter(t => t.includes('✓'))")
        if ticks:
            bad.append(f"stack down ({vp} {view} {scheme}): ticks in the diagram: {ticks[:3]}")
        if view == "circuit":
            pg.screenshot(path=shot)
        pg.close()
    return bad


SEVEN_PARTS = ("bmc-farm", "bcm-mock", "jira-mock", "gpu-exporter", "teleport", "jump", "controller")


def check_services_count(browser, base_url: str, ui_token: str, vp: str, size: tuple[int, int], scheme: str) -> list[str]:
    """One definition of the services count (issue 861 A3, ADR 0089): with six of seven drawn parts answering, the top-strip chip, the
    Services list heading and (where shown) the Overview tile all say 6 of 7. Sample data, no cluster is touched."""
    pg = browser.new_page(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme)
    pg.add_init_script(TEST_HOOK)
    sample = {**STACK_DOWN, "services": {k: k != "jump" for k in SEVEN_PARTS}}
    pg.route("**/api/cluster", lambda r: r.fulfill(json=sample))
    pg.route("**/api/flow*", lambda r: r.abort())
    pg.goto(login_url(base_url, ui_token))
    pg.wait_for_function("typeof setTree === 'function'", timeout=15000)
    pg.wait_for_function("document.querySelector('#c-svc').textContent.includes('/')", timeout=15000)
    pg.wait_for_timeout(500)
    got = pg.evaluate("[document.querySelector('#c-svc').textContent, document.querySelector('#cnt-svc').textContent, document.querySelector('#kpis').textContent]")
    pg.close()
    bad = []
    if "6/7" not in got[0]:
        bad.append(f"services count ({vp} {scheme}): the strip chip says {got[0]!r}, expected 6/7")
    if "6 of 7" not in got[1]:
        bad.append(f"services count ({vp} {scheme}): the Services list says {got[1]!r}, expected 6 of 7")
    if got[2].strip() and "6 of 7" not in got[2].replace("\xa0", " "):
        bad.append(f"services count ({vp} {scheme}): the Overview tile says {got[2]!r}, expected 6 of 7")
    return bad


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def capture(out: Path) -> dict:
    """{"shots": [paths], "console_errors": [...], "overflow": [...], "a11y": [...]}"""
    out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "webui", str(port)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    result: dict = {"shots": [], "console_errors": [], "overflow": [], "a11y": []}
    try:
        base_url = proc.stdout.readline().split("UI: ")[1].strip().removesuffix("/login")
        ui_token = (ROOT / "compose/generated/secrets/ui_token").read_text().strip()  # a one-time code, no token in the URL (#466)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            first = browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")  # nothing stored: dark first; the one page that keeps the CSP (#517)
            first.on("console", lambda m, r=result: r["a11y"].append("CSP violation: " + m.text[:160]) if "Content Security Policy" in m.text else None)
            first.goto(login_url(base_url, ui_token))
            first.wait_for_timeout(500)
            for tab in first.locator("nav a, nav button, [role=tab]").all()[:12]:  # visit each screen so its templates run under the policy
                with contextlib.suppress(PlaywrightError):  # a tab that is hidden or covered is not what this loop checks
                    tab.click(timeout=1500)
                    first.wait_for_timeout(400)
            if first.evaluate("document.documentElement.dataset.theme") != "midnight":
                result["a11y"].append("a first visit does not start in the dark theme")
            first.close()
            for theme in ("light", "dark", "midnight", "dim"):      # no flash: with the app's own scripts blocked, <html> already carries the saved theme
                fp = browser.new_page(viewport={"width": 1280, "height": 900}, bypass_csp=True)
                fp.route("**/static/js/main.js", lambda route: route.abort())
                fp.add_init_script(f"localStorage.setItem('workbench-theme','{theme}')")
                fp.goto(login_url(base_url, ui_token))
                fp.wait_for_timeout(400)
                if fp.evaluate("document.documentElement.dataset.theme") != theme:
                    result["a11y"].append(f"theme flash: saved {theme}, but the first paint has no theme (theme-init.js did not run)")
                fp.close()
            for theme in ("light", "dark"):      # the Circuit view draws with the canvas tokens on hosts that are not .canvas elements (issue 648)
                cp = browser.new_page(viewport={"width": 1280, "height": 900}, bypass_csp=True)
                cp.add_init_script(TEST_HOOK.replace("sessionStorage.setItem('workbench-circuit', '0');", "sessionStorage.setItem('workbench-circuit', '1');"))
                cp.add_init_script(f"localStorage.setItem('workbench-theme','{theme}')")
                cp.goto(login_url(base_url, ui_token))
                cp.wait_for_timeout(1500)
                cp.wait_for_function("typeof setTree === 'function'", timeout=15000)
                cp.evaluate(SYNTH_CLUSTER)
                cp.wait_for_timeout(800)
                for problem in cp.evaluate("""() => { const out = [];
                    for (const el of document.querySelectorAll('#flow[data-cv], .cvlayer, .cvcap, .cvstack, .zoomrail, .canvas'))
                      if (!getComputedStyle(el).getPropertyValue('--cv-ground').trim()) out.push((el.id || el.className.baseVal || el.className) + ' has no --cv-ground');
                    if (!document.querySelector('#flow[data-cv]')) out.push('the circuit did not draw');
                    for (const r of document.querySelectorAll('#flow[data-cv] .cv-box')) if (getComputedStyle(r).fill === 'rgb(0, 0, 0)') { out.push('a box is filled black'); break; }
                    return out; }"""):
                    result["a11y"].append(f"circuit {theme}: {problem}")
                cp.click("#zoomrail [data-level=traffic]")      # Traffic shows the links, one level deeper Requests shows the river, never both (issue 650)
                cp.wait_for_timeout(1200)
                seen = [cp.evaluate("document.getElementById('tab-traffic').dataset.tl")]
                cp.click("#zr-in")
                cp.wait_for_timeout(900)
                seen.append(cp.evaluate("document.getElementById('tab-traffic').dataset.tl"))
                shown = cp.evaluate("getComputedStyle(document.querySelector('#tab-traffic .tan')).display !== 'none'")
                cp.click("#tr-back")
                cp.wait_for_timeout(700)
                seen.append(cp.evaluate("document.getElementById('tab-traffic').dataset.tl"))
                if seen != ["links", "requests", "links"] or not shown:
                    result["a11y"].append(f"circuit {theme}: zoom levels Traffic -> Requests -> Back gave {seen}, river shown {shown}")
                cp.close()
            for scheme in ("light", "dark"):
                for vp, (w, h) in VIEWPORTS.items():
                    page = browser.new_page(viewport={"width": w, "height": h}, color_scheme=scheme, bypass_csp=True)
                    page.add_init_script(TEST_HOOK)
                    page.add_init_script("localStorage.setItem('workbench-theme','auto')")  # the shots follow the system scheme; the default is checked above
                    page.on("console", lambda m, r=result: r["console_errors"].append(m.text + " " + m.location.get("url", "")) if m.type == "error" else None)
                    page.on("pageerror", lambda e, r=result: r["console_errors"].append(str(e)))
                    deadline = time.time() + 15
                    while True:
                        try:
                            page.goto(login_url(base_url, ui_token))
                            break
                        except Exception:
                            if time.time() > deadline:
                                raise
                            time.sleep(0.3)
                    page.wait_for_timeout(2000)
                    page.wait_for_function("typeof setTree === 'function'", timeout=15000)  # main.js loads the test hook after its own imports
                    page.evaluate("setTree(true, false)")  # a 1280 px window starts with the list folded; the checks below look at the list
                    for problem in check_wire(page):
                        result["a11y"].append(f"wire component {vp} {scheme}: {problem}")
                    path = out / f"dashboard-{vp}-{scheme}.png"
                    page.screenshot(path=str(path), full_page=True)
                    result["shots"].append(str(path))
                    if page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"):
                        result["overflow"].append(f"dashboard {vp} {scheme}")
                    for sel in ("#banner", "#attn", "#list .btn", "#services .er", "#flow", ".node"):
                        if page.locator(sel).count() == 0:
                            result["a11y"].append(f"dashboard is missing {sel}")
                    down = "simulation stack is not running" in page.locator("#attn").inner_text()  # stack down: one notice, nothing repeating it
                    for sel in ("#svcpanel", "#br-compute"):
                        if page.locator(sel).is_visible() == down:
                            result["a11y"].append(f"{sel} must be {'hidden' if down else 'shown'} when the stack is {'down' if down else 'up'}")
                    page.keyboard.press("Tab")  # the skip link is the first stop
                    if not page.evaluate("document.activeElement.className === 'skip'"):
                        result["a11y"].append("skip link is not the first tab stop")
                    # landmarks and heading, theme toggle
                    if page.locator("main#main").count() != 1 or page.locator("header.top h1").count() != 1 or page.locator("aside").count() != 0:
                        result["a11y"].append("missing main landmark or page heading")
                    page.click("#theme")
                    if page.evaluate("document.documentElement.dataset.theme") != "light":
                        result["a11y"].append("theme toggle did not switch to light")
                    page.click("#theme")
                    if page.evaluate("document.documentElement.dataset.theme") != "dark":
                        result["a11y"].append("theme toggle did not switch to dark")
                    for problem in check_circuit(page):
                        result["a11y"].append(f"circuit {vp} {scheme}: {problem}")
                    rpath = out / f"runlayer-{vp}-{scheme}.png"
                    for problem in check_runlayer(page, str(rpath), vp):
                        result["a11y"].append(f"run layer {vp} {scheme}: {problem}")
                    result["shots"].append(str(rpath))
                    for problem in check_shell(page):
                        result["a11y"].append(f"shell {vp} {scheme}: {problem}")
                    for problem in check_home(page):
                        result["a11y"].append(f"overview {vp} {scheme}: {problem}")
                    hpath = out / f"overview-{vp}-{scheme}.png"
                    page.click("#tb-home")
                    page.screenshot(path=str(hpath), full_page=True)
                    result["shots"].append(str(hpath))
                    page.click("#tb-traffic")  # documented links, real or simulated, observed or configured
                    page.wait_for_selector("#tr-rows tr td:nth-child(3)", state="attached")
                    for problem in check_analysis(page):
                        result["a11y"].append(f"analysis {vp} {scheme}: {problem}")
                    tpath = out / f"traffic-{vp}-{scheme}.png"
                    page.screenshot(path=str(tpath), full_page=True)
                    result["shots"].append(str(tpath))
                    if page.locator("#tr-rows tr").count() < 5:
                        result["a11y"].append("traffic tab lists fewer than 5 links")
                    page.click("#tr-chips [data-plane] >> nth=1")  # a plane chip narrows the rows, All restores them
                    if page.locator("#tr-rows tr").count() >= page.evaluate("trRows.length") + 1:
                        result["a11y"].append("plane chip did not narrow the links")
                    page.click("#tr-chips [data-plane=all]")
                    page.click("#tr-zin")
                    if page.evaluate("trZoom") <= 1:
                        result["a11y"].append("zoom in did nothing")
                    page.click("#tr-zfit")
                    page.click("#tr-map [data-link] >> nth=0", force=True)  # inspector, then the path through that link
                    page.wait_for_selector("#tr-insp:not([hidden])")
                    page.click("#tr-follow")
                    page.wait_for_selector("#tr-path:not([hidden])")
                    if page.locator("#tr-rows tr").count() < 1:
                        result["a11y"].append("following a path left no links")
                    page.click("#tr-clear")
                    page.click("#tb-conn")  # connections tab: every entity listed with an address and a credential location
                    page.wait_for_selector("#conn-rows tr td code")
                    cpath = out / f"connections-{vp}-{scheme}.png"
                    page.screenshot(path=str(cpath), full_page=True)
                    result["shots"].append(str(cpath))
                    if page.locator("#conn-rows tr").count() < 8:
                        result["a11y"].append("connections tab lists fewer than 8 parts")
                    page.click("#tb-nodes")  # traffic sits beside the nodes: clients, services and node tiles in one diagram
                    page.wait_for_selector("#flow rect.blk")
                    if page.locator("#flow .nd").count() < 1 or page.locator("#flow text:has-text('Monitoring app')").count() != 1:
                        result["a11y"].append("Infrastructure architecture is missing the node tiles or the clients")
                    page.wait_for_timeout(450)  # the diagram fits itself to the panel once it has settled (#319); a rebuild would drop packets already in flight
                    page.evaluate("""onFlow({level: 0, cursor: 9, events: [{from: 'clients', to: 'bmc-farm', n: 3, err: 2, label: 'requests', nodes: []},
                        {from: 'clients', to: 'jira-mock', n: 2, label: 'requests', nodes: []}, {from: 'clients', to: 'teleport', n: 2, label: 'requests', nodes: []}, {from: 'injector', to: 'bmc-farm', n: 1, label: 'overheat on cn002', nodes: ['cn002']}],
                        errors: {'bmc-farm': 2}, totals: {'bmc-farm': 3, 'bcm-mock': 0, 'jira-mock': 2, 'gpu-exporter': 0, teleport: 2}, reachable: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'jira-real': true, 'gpu-exporter': true, teleport: true}})""")
                    page.wait_for_timeout(450)  # packets in flight
                    if not page.evaluate("flowPaused || reduced.matches") and page.locator("#dots .pk").count() < 1:
                        result["a11y"].append("traffic animation drew no packets for synthetic events")
                    if not page.evaluate("flowPaused || reduced.matches") and page.locator("#dots polygon.pk.err").count() < 1:
                        result["a11y"].append("an error answer was not drawn as a triangle dot (#314)")
                    if "2 errors" not in page.text_content("#timeline-feed") or "2 errors" not in page.evaluate("document.getElementById('fc-bmc-farm').textContent"):
                        svc_text = page.evaluate("document.getElementById('fc-bmc-farm').textContent")
                        result["a11y"].append(f"errors have no text equivalent in the feed and the service box (#314): {page.text_content('#timeline-feed')[:80]!r} / {svc_text!r}")
                    b = page.evaluate("({marked: document.querySelectorAll('#flow [data-blast]').length, lanes: document.querySelectorAll('#flow path.lane[data-blast]').length, text: $('blastpane').hidden ? '' : $('blastpane').textContent})")
                    if b["marked"] < 3 or b["lanes"] < 1 or "declared" not in b["text"] or "BMC farm" not in b["text"]:
                        result["a11y"].append(f"blast radius did not mark the affected parts and say it is declared (#315): {b}")
                    drawn = set(page.evaluate("[...document.querySelectorAll('#flow [data-edge]')].map(e => e.dataset.edge)"))
                    declared = set(page.evaluate("JSON.parse(lastCluster).graph.edges.map(e => e.id)"))  # the model the page was given
                    if drawn != declared:  # the drawing and the declared graph must agree (#312)
                        result["a11y"].append(f"architecture draws {sorted(drawn ^ declared)} differently from the declared graph")
                    tpath = out / f"architecture-{vp}-{scheme}.png"
                    page.locator("#trafficpanel").screenshot(path=str(tpath))
                    result["shots"].append(str(tpath))
                    page.click("#keybtn")  # the key is a popover under the Key button: opens, shows the line styles, Escape closes it and gives the focus back
                    if page.locator("#ckey:visible").count() != 1 or page.get_attribute("#keybtn", "aria-expanded") != "true" or page.locator("#ckey .ls").count() != 4:
                        result["a11y"].append("the key did not open with its four line samples")
                    page.locator("#trafficpanel").screenshot(path=str(out / f"key-{vp}-{scheme}.png"))  # not in "shots": the count of reviewed screens stays
                    page.keyboard.press("Escape")
                    if page.locator("#ckey:visible").count() or page.evaluate("document.activeElement.id") != "keybtn":
                        result["a11y"].append("Escape did not close the key and return the focus to its button")
                    page.click("#lg-login")  # every part opens a pop-up with its details; Escape closes it and gives the focus back
                    if page.locator("#pop:visible dl").count() != 1:
                        result["a11y"].append("clicking a login node did not open its pop-up")
                    page.keyboard.press("Escape")
                    if page.locator("#pop:visible").count() != 0 or not page.evaluate("document.activeElement.id === 'lg-login'"):
                        result["a11y"].append("Escape did not close the pop-up and return the focus")
                    if vp == "desktop":
                        page.hover("#lg-login")
                        if page.locator("#tip:visible").count() != 1:
                            result["a11y"].append("hovering a part showed no tip")
                        page.mouse.move(2, 2)
                        page.evaluate(SYNTH_CLUSTER)  # with the stack down (a --full run, after the other modules tore theirs down) the list is hidden for want of nodes
                        page.wait_for_timeout(300)
                        page.click("[data-sub=topo]")  # the node list folds away, the diagram keeps the width
                        if page.locator("#nodesmain:visible").count() != 0:
                            result["a11y"].append("the node list did not fold away")
                        page.click("[data-sub=all]")
                        if page.locator("#nodesmain:visible").count() != 1:
                            result["a11y"].append("the node list did not come back")
                    if vp == "phone" and page.evaluate("document.querySelector('#nodeswrap').getBoundingClientRect().width < 300 || getComputedStyle(document.querySelector('#nodeswrap')).gridTemplateColumns.split(' ').length > 1"):
                        result["a11y"].append("phone layout must stack traffic and the list in one column")
                    if vp == "desktop":  # one screen: with six nodes drawn, traffic and tree both fit without page scrolling
                        page.evaluate(SYNTH_CLUSTER)
                        page.wait_for_timeout(300)
                        if page.evaluate("document.documentElement.scrollHeight > window.innerHeight + 2"):
                            result["a11y"].append("dashboard needs page scrolling at desktop size")
                    page.click("#menubtn")  # the top menu: opens as a disclosure, stays on screen, Escape closes it and returns focus
                    if page.get_attribute("#menubtn", "aria-expanded") != "true" or page.locator("#menu:visible").count() != 1:
                        result["a11y"].append("menu did not open")
                    mbox = page.locator("#menu").bounding_box() or {"x": -1, "y": -1, "width": 0, "height": 0}
                    if mbox["x"] < 0 or mbox["x"] + mbox["width"] > w + 1 or mbox["y"] + mbox["height"] > h + 1:
                        result["a11y"].append(f"menu does not fit the {vp} screen")
                    mpath = out / f"menu-{vp}-{scheme}.png"
                    page.screenshot(path=str(mpath))
                    result["shots"].append(str(mpath))
                    page.keyboard.press("Escape")
                    if page.locator("#menu:visible").count() or page.evaluate("document.activeElement.id") != "menubtn":
                        result["a11y"].append("Escape must close the menu and return focus to the Menu button")
                    sdpath = out / f"stackdown-{vp}-{scheme}.png"
                    for problem in check_stack_down(browser, base_url, ui_token, vp, (w, h), scheme, str(sdpath)):
                        result["a11y"].append(problem)
                    result["shots"].append(str(sdpath))
                    for problem in check_services_count(browser, base_url, ui_token, vp, (w, h), scheme):
                        result["a11y"].append(problem)
                    spath = out / f"testrun-{vp}-{scheme}.png"
                    for problem in check_run(page, str(spath)):
                        result["a11y"].append(problem)
                    result["shots"].append(str(spath))
                    page.click("#planbtn")  # planner sheet: opens, estimates, reacts to a stepper press, shows the command
                    page.wait_for_function("document.querySelector('#pl-msg').textContent.length > 5")
                    page.click("[data-step=pl-cpu][data-d='-1']")
                    page.wait_for_function("document.querySelector('#pl-cmd').textContent.includes('--cpu-nodes') || document.querySelector('#pl-cmd').textContent.startsWith('Does not fit')")
                    page.wait_for_function("document.querySelector('#pl-review').textContent.includes('Removes')")  # 6d: the review names what Apply removes and restarts
                    rv = page.evaluate("({lines: document.querySelector('#pl-review').textContent, jobs: !document.querySelector('#pl-jobs').hidden, apply: document.querySelector('#pl-apply').disabled})")
                    if "cn004" not in rv["lines"] or "slurmctld" not in rv["lines"] or not rv["jobs"]:
                        result["a11y"].append(f"plan review ({vp} {scheme}): {rv}")
                    # a removed node with jobs: the tick gates Apply. The sheet's own read of the node states can land after the first set and hide the tick again, so set,
                    # read and tick in one step, repeated until the row is shown
                    page.wait_for_function("""(() => { planStates = {cn004: 'allocated'}; paintReview(); if (document.querySelector('#pl-ackrow').hidden) return false;
                      window.__ak = {row: true, blocked: document.querySelector('#pl-apply').disabled, stop: document.querySelector('#pl-jobs').className.includes('stop')};
                      const t = document.querySelector('#pl-ack'); t.checked = true; t.dispatchEvent(new Event('change')); return true; })()""")
                    ak = page.evaluate("window.__ak")
                    if not (ak["row"] and ak["blocked"] and ak["stop"]) or page.evaluate("document.querySelector('#pl-apply').disabled"):
                        result["a11y"].append(f"plan review: running jobs must block Apply until ticked ({vp} {scheme}): {ak}")
                    page.evaluate("planStates = null; paintReview()")
                    page.evaluate("(() => { change = {job: 7, label: 'apply-plan', state: 'running', started: 1, finished: null, stages: [{name: 'Build images', state: 'done', secs: 12.4}, {name: 'Nodes', state: 'running', secs: null}, {name: 'Login node', state: 'waiting', secs: null}]}; paintProgress(7); })()")  # U5: the governor's stages, not the log text
                    pg = page.evaluate("({shown: !document.querySelector('#pl-prog').hidden, rows: [...document.querySelectorAll('#pl-stages li')].map(l => [...l.children].map(c => c.textContent).join(' ')), msg: document.querySelector('#pl-apply-msg').textContent})")
                    if not pg["shown"] or pg["rows"] != ["✓ Build images 12 s", "▶ Nodes running", "○ Login node waiting"] or "1 of 3 stages done" not in pg["msg"]:
                        result["a11y"].append(f"apply progress ({vp} {scheme}): {pg}")
                    ppath = out / f"plan-{vp}-{scheme}.png"
                    page.screenshot(path=str(ppath))
                    result["shots"].append(str(ppath))
                    box = page.locator("#plandlg").bounding_box()
                    if box["x"] < -1 or box["x"] + box["width"] > w + 1:
                        result["overflow"].append(f"plan sheet {vp} {scheme}")
                    page.click("#plan-close")
                    if page.locator("#plandlg[open]").count():
                        result["a11y"].append("plan sheet did not close")
                    page.click("#tb-nodes")
                    page.evaluate(SYNTH_CLUSTER)
                    page.evaluate("showNode('cn001')")  # node panel replaces the list in place: info and actions as two cards, traffic stays visible
                    page.wait_for_selector("#nodepane:not([hidden])")
                    page.wait_for_function("document.querySelector('#nodetext').textContent.includes('Name:')")
                    dpath = out / f"node-details-{vp}-{scheme}.png"
                    page.screenshot(path=str(dpath), full_page=True)
                    result["shots"].append(str(dpath))
                    if page.locator("dialog[open]").count() or not page.locator("#flow").is_visible() and vp == "desktop":
                        result["a11y"].append("node panel must not cover the Infrastructure architecture")
                    sm = page.evaluate("({keys: [...document.querySelectorAll('#nodesum dt')].map(e => e.textContent), reach: $('nodereach').hidden, chips: [...document.querySelectorAll('#lens [data-lens]')].map(b => b.getAttribute('aria-pressed'))})")  # 6c: summary first, a reach command, one pressed lens chip
                    if not {"Reason", "Hardware", "Power", "Address", "BMC"} <= set(sm["keys"]) or sm["reach"] or sm["chips"].count("true") != 1:
                        result["a11y"].append(f"node summary or lens chips: {sm}")
                    if vp == "desktop" and page.locator("#nodeacts-fault .btn").count() < 3:
                        result["a11y"].append("node panel is missing its fault actions")
                    page.click("#node-back")
                    if not page.locator("#nodepane").is_hidden() or page.locator("#tree").is_hidden():
                        result["a11y"].append(f"node panel did not close ({vp} {scheme}: panel hidden={page.locator('#nodepane').is_hidden()} tree hidden={page.locator('#tree').is_hidden()})")
                    if vp == "desktop" and scheme == "dark":
                        result["a11y"].extend(check_hidden(page))
                    page.close()
            for w, h, count in ((1280, 900, 6), (1920, 1080, 50)):  # the architecture is the page (#319): it fills the width, the page itself never scrolls, the rails scroll
                page = browser.new_page(viewport={"width": w, "height": h}, bypass_csp=True)
                page.add_init_script(TEST_HOOK)
                page.goto(login_url(base_url, ui_token))
                page.wait_for_selector("#flow rect.blk")
                page.wait_for_timeout(1200)  # the first real poll has landed: it must not overwrite the sample
                page.evaluate(SYNTH_CLUSTER)
                page.evaluate("""n => { const c = JSON.parse(lastCluster); c.nodes = Array.from({length: n}, (_, i) => ({name: 'cn' + String(i + 1).padStart(3, '0'), kind: 'compute', state: 'ok', label: 'Healthy', temp: 45, power: 'On', gpus: 0}));
                    c.graph.entities = c.graph.entities.filter(e => !e.group).concat(c.nodes.map(x => ({id: x.name, type: 'compute-node', group: 'nodes'}))); lastCluster = JSON.stringify(c); renderCluster(c); buildFlow(); }""", count)
                page.wait_for_timeout(400)
                m = page.evaluate("""() => ({scroll: document.documentElement.scrollHeight - innerHeight, flow: document.querySelector('#flow').getBoundingClientRect().width,
                    list: document.querySelector('#nodesmain').getBoundingClientRect().height, insp: document.querySelector('#inspector').getBoundingClientRect().width,
                    fit: (() => { const f = document.querySelector('#flow'), v = f.viewBox.baseVal, r = f.getBoundingClientRect(); return (v.width / v.height) / (r.width / r.height); })()})""")
                if count == 6 and not 0.85 <= m["fit"] <= 1.18:  # the drawing is built from the panel's shape (#319): a ratio far from 1 means bars of empty space beside or above it
                    result["a11y"].append(f"the diagram does not fit its panel at {w}x{h}: drawing/panel aspect ratio {m['fit']:.2f}")
                if m["scroll"] > 2:
                    result["a11y"].append(f"dashboard scrolls the page at {w}x{h} with {count} nodes")
                if m["flow"] < w * (0.42 if w < 1500 else 0.48):
                    result["a11y"].append(f"the architecture is only {round(m['flow'])} px wide at {w}x{h}: the canvas should use the free space")
                if m["list"] < 120 or m["insp"] < 300:
                    result["a11y"].append(f"node list or inspector too small at {w}x{h}: {m}")
                if count == 6:  # focus view (#313): a part and its neighbours stay, the rest dims, nothing moves, Esc returns
                    rel0 = page.evaluate("[...document.querySelectorAll('#flow [data-ent]')].map(e => { const r = e.getBoundingClientRect(), o = document.querySelector('#flow').getBoundingClientRect(); return [e.getAttribute('data-ent'), Math.round(r.x - o.x), Math.round(r.y - o.y)]; })")  # positions inside the diagram: clicking a chip may scroll the page, which is not a move
                    before = page.evaluate("[...document.querySelectorAll('#flow [data-ent]')].map(e => { const r = e.getBoundingClientRect(); return [e.getAttribute('data-ent'), Math.round(r.x), Math.round(r.y)]; })")
                    page.locator("#flow g[data-ent='login']").dblclick(); page.wait_for_timeout(300)
                    f = page.evaluate("({dim: document.querySelectorAll('#flow [data-dim]').length, crumb: !!document.querySelector('#crumb:not([hidden])'), rows: document.querySelectorAll('#focuspane tbody tr').length, faulted: document.querySelectorAll('#flow [data-dim] .nd.fault').length})")
                    after = page.evaluate("[...document.querySelectorAll('#flow [data-ent]')].map(e => { const r = e.getBoundingClientRect(); return [e.getAttribute('data-ent'), Math.round(r.x), Math.round(r.y)]; })")
                    if f["dim"] < 3 or not f["crumb"] or f["rows"] < 2 or f["faulted"]:
                        result["a11y"].append(f"focus view: {f}")
                    if before != after:
                        result["a11y"].append("focus view moved a part of the diagram")
                    page.keyboard.press("Escape"); page.wait_for_timeout(200)
                    for choice in ("management", "client"):  # lenses (#316): the layout never moves; the client's eye reads only real counters
                        page.click(f"#lens [data-lens={choice}]"); page.wait_for_timeout(250)
                        li = page.evaluate("({dim: document.querySelectorAll('#flow [data-ldim]').length, eye: $('eyepane').hidden ? '' : $('eyepane').textContent, pos: [...document.querySelectorAll('#flow [data-ent]')].map(e => { const r = e.getBoundingClientRect(), o = document.querySelector('#flow').getBoundingClientRect(); return [e.getAttribute('data-ent'), Math.round(r.x - o.x), Math.round(r.y - o.y)]; })})")
                        if li["dim"] < 2 or li["pos"] != rel0:
                            result["a11y"].append(f"lens {choice} dimmed nothing or moved a part (dimmed {li['dim']}, moved {[(a, b) for a, b in zip(rel0, li['pos']) if a != b][:3]})")
                        if choice == "client" and ("not measured" not in li["eye"] or "BMC farm" not in li["eye"] or "Verdict" not in li["eye"]):
                            result["a11y"].append("client's-eye lens did not list the services with a verdict and 'not measured' where nothing counts")
                    page.click("#lens [data-lens=all]"); page.wait_for_timeout(200)
                    if page.evaluate("document.querySelectorAll('#flow [data-ldim]').length"):
                        result["a11y"].append("lens All planes left parts dimmed")
                    page.locator("#flow g[data-ent='login']").dblclick(); page.wait_for_timeout(200)  # focus again for the checks below
                    page.click("#vw-cv"); page.wait_for_timeout(300)  # the view switcher: the circuit and the classic diagram; the focus stays
                    if page.evaluate("focusId") != "login" or page.get_attribute("#vw-cv", "aria-pressed") != "true":
                        result["a11y"].append("switching to the circuit lost the focus or did not change the view")
                    page.click("#vw-iso"); page.wait_for_timeout(400)
                    if page.locator("#flow .nd").count() != 6 or page.get_attribute("#vw-iso", "aria-pressed") != "true":
                        result["a11y"].append("the classic button of the view switcher did not return to the 2.5D diagram")
                    page.locator("#flow g[data-ent='login']").dblclick(); page.wait_for_timeout(200)  # focus again for the Esc check
                    page.keyboard.press("Escape"); page.wait_for_timeout(200)
                    if page.evaluate("document.querySelectorAll('#flow [data-dim]').length") or page.evaluate("focusId"):
                        result["a11y"].append("Esc did not leave the focus view")
                level = page.evaluate("document.querySelector('#flow .ovcard') ? 'overview' : 'full'")  # the global view (#321): small clusters open at the full diagram, large ones at the overview
                if level != ("full" if count == 6 else "overview"):
                    result["a11y"].append(f"{count} nodes opened at the {level} level")
                if count == 50:
                    if not 0.9 <= m["fit"] <= 1.1:
                        result["a11y"].append(f"the overview does not fill its panel at {w}x{h}: ratio {m['fit']:.2f}")
                    page.hover("#flow .ovcard"); page.wait_for_timeout(150)
                    if not page.locator("#ovprev:visible").count():
                        result["a11y"].append("hovering the cluster card showed no preview of the design underneath")
                    page.mouse.move(2, 2); page.dblclick("#flow .ovcard"); page.wait_for_timeout(500)
                    if page.locator("#flow .nd").count() != 50 or page.locator("#crumb:visible").count() != 1:
                        result["a11y"].append("double-click did not open the full diagram with a breadcrumb")
                    page.keyboard.press("Escape"); page.wait_for_timeout(400)
                    if not page.locator("#flow .ovcard").count():
                        result["a11y"].append("Esc did not return to the overview")
                    page.focus("#flow .ovcard"); page.keyboard.press("Enter"); page.wait_for_timeout(400)
                    if page.locator("#flow .nd").count() != 50:
                        result["a11y"].append("Enter on the cluster card did not open the full diagram")
                    page.click("#crumb-all"); page.wait_for_timeout(300)
                    if not page.locator("#flow .ovcard").count() or not page.locator("#ovtable table").count():
                        result["a11y"].append("the breadcrumb did not return to the overview, or the cluster table is missing")
                page.close()
            for w, h, count in ((1280, 900, 2), (1280, 900, 12), (1920, 1080, 12)):  # several clusters (#327): a grid of cards that fits the panel; entering one shows that cluster
                page = browser.new_page(viewport={"width": w, "height": h}, bypass_csp=True)
                page.add_init_script(TEST_HOOK)
                page.goto(login_url(base_url, ui_token))
                page.wait_for_selector("#flow rect.blk")
                page.wait_for_timeout(1200)
                page.evaluate(SYNTH_CLUSTER)
                page.evaluate("""n => { const c = JSON.parse(lastCluster), base = c.nodes;
                    c.clusters = Array.from({length: n}, (_, i) => ({name: 'cluster-' + (i + 1), nodes: base.slice(0, i === 1 ? 3 : base.length), logins: c.logins, services: c.services}));
                    lastCluster = JSON.stringify(c); renderCluster(c); setLevel('overview', false); }""", count)
                page.wait_for_timeout(400)
                m = page.evaluate("() => ({scroll: document.documentElement.scrollHeight - innerHeight, cards: document.querySelectorAll('#flow .ovcard').length, rows: document.querySelectorAll('#ovtable tbody tr').length, over: curLevel()})")
                if m["scroll"] > 2 or m["cards"] != count or m["rows"] != count or m["over"] != "overview":
                    result["a11y"].append(f"{count} clusters at {w}x{h}: {m}")
                page.locator("#flow .ovcard").nth(1).dblclick(); page.wait_for_timeout(500)
                m = page.evaluate("() => ({nodes: document.querySelectorAll('#flow .nd').length, crumb: document.querySelector('#crumb-name').textContent, list: document.querySelectorAll('#tree .er.node, #tree [data-node]').length})")
                if m["nodes"] != 3 or m["crumb"] != "cluster-2":
                    result["a11y"].append(f"entering cluster-2 of {count} at {w}x{h} did not show its own diagram: {m}")
                page.keyboard.press("Escape"); page.wait_for_timeout(300)
                if page.locator("#flow .ovcard").count() != count:
                    result["a11y"].append(f"Esc did not return to the {count}-cluster overview")
                page.close()
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    return result


if __name__ == "__main__":
    res = capture(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/workbench-ui-shots"))
    print("\n".join(res["shots"]))
    for key in ("console_errors", "overflow", "a11y"):
        for item in res[key]:
            print(f"{key}: {item}")
    sys.exit(1 if any(res[k] for k in ("console_errors", "overflow", "a11y")) else 0)
