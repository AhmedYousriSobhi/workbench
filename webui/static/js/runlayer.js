// The run layer (ADR 0086, phase 1): a scenario run drawn on the Circuit view. runlayermodel.js decides what to show; this file draws
// it: numbered badges on the parts each step touched (a number and a glyph, never colour alone), a ring on the chosen step's parts,
// expectation pins, the step rail in the inspector's Run tab, and on a phone a bottom sheet with Previous and Next.
// Phase 2, before Play: the scenario picked in Test run is drawn the same way as ghosts: dashed badges ("planned"), a dotted ring on
// every part it will touch, the pins, and ◎ on each part whose clients may see a change (the declared blast, webui/blast.py). Play
// replaces the ghosts with the live badges in one redraw; Stop, or Take off the diagram, leaves the plain diagram.
// Phase 3, after the run: the strip over the diagram becomes a player, a WAI-ARIA slider over the recorded steps (arrows, Home, End).
// It moves the badges, the ring, the pins and the rail back in time; node states stay live. The run it walks is the page process's run
// state (decision 4: memory only). After Stop the diagram is plain, and the Run tab offers Replay the last run.
// Phase 4: a run from the CLI (`benchctl scenario run`, posted to the page) is drawn the same way, and said: "run from the CLI".
//
// Nothing moves: states change in place, so reduced motion needs nothing special beyond an instant scroll. Badges come from the run
// state the page polls (GET /api/scenarios/run), never from flow events: a mark only where the engine says a step acted.
import { S } from './state.js';
import { $, esc, announce } from './util.js';
import { sv } from './svg.js';
import { reduced } from './flowstate.js';
import { showPane } from './inspector.js';
import { ENT_NAME } from './focus.js';
import { selectTab, setCircuit } from './shell.js';
import { zoomTo } from './zoom.js';
import { RUN_WORD, blastMarks, cliNote, expectPins, focusStep, fromCli, ghostParts, partBadges, partWords, previewRun, previewSummary, railRow, replayCaption, replayClamp,
  replayGroups, replayHeading, replayKey, replayPins, replayStops, replayText, replayable, runGroups, runHeading, runKey, runSig, sheetFor, sourceWords } from './runlayermodel.js';
import { runVerdict } from './testrunmodel.js';

// Sizes are set in screen pixels and turned into drawing units with the drawing's scale (the Circuit view is 1700 units wide and drawn at
// about 0.55 of that on a laptop or a phone), so the text stays readable however small the drawing is shown (issue 813).
const TEXT_PX = 15, HIT_PX = 40, MIN_FONT = 21, MAX_FONT = 40;  // badge text >= 14 px on screen; a 40 px tap target round every badge
let size = { font: MIN_FONT, bh: 36, hit: 40 };
let drawnSig = '', lastCur = null, groups = [], pins = [], recorded = [], stops = [], playTimer = null;  // recorded: the groups as the run ended; stops: replay's steps
const attr = s => String(s).replace(/["\\]/g, '\\$&');
const phone = () => matchMedia('(max-width:640px)').matches;

// What the layer shows, unless the user took it off the diagram: a live run always; else the scenario picked in Test run, as a preview
// until it has run (its last run once it has); else the last run polled.
let pvFrom = null, pvRun = null;
const asPreview = p => p === pvFrom ? pvRun : (pvFrom = p, pvRun = previewRun(p));
export function shownRun() {
  const r = S.trLast && S.trLast.name ? S.trLast : null, p = S.trPrev && S.trPrev.name ? S.trPrev : null;
  const run = r && r.running ? r : p && !(r && r.name === p.name) ? asPreview(p) : r;
  return run && S.runHidden !== runKey(run) ? run : null;
}

// The drawn part for a run id: a node's square (its own, or its group's in a large pool), else the part, else the graph entity.
// A part the cluster lacks (greyed, data-present=false) counts as not drawn.
export function runPartEl(id) {
  const f = $('flow'); if (!f || !f.dataset.cv) return null;
  const a = attr(id), el = f.querySelector(`.cv-sq[data-names~="${a}"]`) || f.querySelector(`[data-part="${a}"]`) || f.querySelector(`.cv-part[data-ent="${a}"]`);
  return el && !el.closest('[data-present="false"]') && !el.closest('#runlayer') ? el : null;
}
const has = id => !!runPartEl(id);

// ---- the layer on the drawing ----
function placeMark(el, w, h, kind, taken) {  // where a badge or pin goes: on a part's top (or bottom) edge; a square's on its rack's edge, over the square
  const b = el.getBBox(), sq = el.classList.contains('cv-sq'), rack = sq ? el.closest('.cv-rack')?.querySelector('.cv-box')?.getBBox() : null;
  const left = !!el.querySelector?.('.cv-badge-bg');  // the login box wears HEAD NODE at its top right
  let x = sq ? b.x + b.width / 2 - w / 2 : kind === 'pin' || left ? b.x + 14 : b.x + b.width - w - 14;
  const y = kind === 'pin' ? (rack ? rack.y + rack.height - 10 : b.y + b.height - 10) : (rack ? rack.y - h + 8 : b.y - h + 10);
  for (const r of taken) if (Math.abs(r.y - y) < h && x < r.x + r.w + 4 && x + w > r.x - 4) x = r.x + r.w + 6;  // never cover another badge
  taken.push({ x, y, w, h });
  return { x, y };
}

// ◎ sits over a part's top-left corner, else its top-right, else its bottom-left one; where both are covered it is left out (the rail says it in words).
function placeBlast(el, d, taken) {
  const b = el.getBBox(), free = (x, y) => !taken.some(r => x < r.x + r.w + 4 && x + d > r.x - 4 && y < r.y + r.h + 4 && y + d > r.y - 4);
  for (const [x, y] of [[b.x - d * 0.6, b.y - d * 0.6], [b.x + b.width - d * 0.4, b.y - d * 0.6], [b.x - d * 0.6, b.y + b.height - d * 0.4]]) if (free(x, y)) { taken.push({ x, y, w: d, h: d }); return { x, y }; }  // over the corner, clear of the title
  return null;
}

function markSize(f) {  // the badge size in drawing units for the scale the drawing is shown at now
  const k = f.getScreenCTM?.()?.a || 0.6, font = Math.round(Math.min(MAX_FONT, Math.max(MIN_FONT, TEXT_PX / k)));
  return { font, bh: Math.round(font * 1.7), hit: Math.ceil(HIT_PX / k) };
}

function blastEl(el, label, taken) {  // ◎: clients of this part may see a change (declared, not measured). Information only, so not a tab stop
  const d = Math.round(size.bh * 0.9), at = placeBlast(el, d, taken); if (!at) return null;
  const g = sv('g', { class: 'rl-blast', role: 'img', 'aria-label': label });
  g.append(sv('title', {}, label), sv('circle', { class: 'rl-pill', cx: at.x + d / 2, cy: at.y + d / 2, r: d / 2 }),
    sv('text', { class: 'rl-t', x: at.x + d / 2, y: at.y + d / 2 + size.font * 0.36, 'text-anchor': 'middle' }, '◎'));
  return g;
}

function markEl(cls, text, more, aria, n, onAct) {
  const g = sv('g', { class: cls, role: 'button', tabindex: '0', 'aria-label': aria, 'data-n': n });
  g.append(sv('title', {}, aria), sv('rect', { class: 'rl-hit', height: Math.max(size.bh, size.hit) }), sv('rect', { class: 'rl-pill', height: size.bh, rx: size.bh / 2 }), sv('text', { class: 'rl-t' }, text));
  if (more) g.append(sv('text', { class: 'rl-more' }, more));
  g.addEventListener('click', e => { e.stopPropagation(); onAct(); });
  g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onAct(); } });
  return g;
}
function fitMark(g, el, kind, taken) {  // size the pill to its text, then place it; the hit area round it is at least 40 px on screen
  const { font, bh, hit } = size, pad = Math.round(font * 0.62), gap = Math.round(font * 0.38);
  const [t, m] = [g.querySelector('.rl-t'), g.querySelector('.rl-more')], tw = t.getComputedTextLength(), mw = m ? m.getComputedTextLength() + gap : 0, w = Math.ceil(tw + mw + 2 * pad);
  const { x, y } = placeMark(el, w, bh, kind, taken), r = g.querySelector('.rl-pill'), h = g.querySelector('.rl-hit'), hw = Math.max(w, hit), hh = Math.max(bh, hit);
  r.setAttribute('x', x); r.setAttribute('y', y); r.setAttribute('width', w);
  if (h) { h.setAttribute('x', x - (hw - w) / 2); h.setAttribute('y', y - (hh - bh) / 2); h.setAttribute('width', hw); }
  const base = y + bh / 2 + font * 0.36;  // the text's baseline: centred in the pill
  t.setAttribute('x', x + pad); t.setAttribute('y', base);
  if (m) { m.setAttribute('x', x + pad + tw + gap); m.setAttribute('y', base); }
}

export function paintRunLayer(force) {
  const f = $('flow'), run = shownRun();
  if (!f) return;
  const sig = f.dataset.cv && run ? runSig(run) + '|' + (f.querySelector('.cv-box') ? 'b' : '') + (S.replay ? `|r${S.replay.at}` : '') : '';
  if (!force && sig === drawnSig && (!sig || f.querySelector('#runlayer'))) { ringStep(); return; }
  drawnSig = sig;
  const keep = document.activeElement && document.activeElement.closest?.('#runlayer') ? document.activeElement.getAttribute('data-key') : null;
  f.querySelector('#runlayer')?.remove();
  if (!sig) { ringStep(); return; }
  const layer = sv('g', { id: 'runlayer', class: `runlayer${run.preview ? ' preview' : ''}`, role: 'group',
    'aria-label': `Scenario ${run.name}${fromCli(run) ? `, ${sourceWords(run)}` : ''}: ${run.preview ? 'preview, nothing has run yet; planned' : ''} step badges, in step order` }), taken = [];
  size = markSize(f); layer.style.setProperty('--rl-fs', `${size.font}px`);
  f.append(layer);
  for (const b of partBadges(groups, has)) {  // in step order, so Tab follows the scenario
    const el = runPartEl(b.id), g = markEl(`rl-badge k-${b.state}${b.current ? ' cur' : ''}${b.ghost ? ' ghost' : ''}`, `${b.n} ${b.glyph}`, b.more ? `+${b.more}` : '', b.label, b.n, () => chooseStep(b.n, true));
    g.setAttribute('data-key', 'b:' + b.id); g.setAttribute('data-id', b.id); layer.append(g); fitMark(g, el, 'badge', taken);
  }
  for (const p of pins) {
    const el = p.ids.map(runPartEl).find(Boolean); if (!el) continue;
    const g = markEl(`rl-pin k-${p.state}${run.preview ? ' ghost' : ''}`, `E${p.e} ${p.glyph}`, '', p.aria, '', () => { showPane('run'); $('ipane-run')?.querySelector(`[data-e="${p.e}"]`)?.focus(); });
    g.setAttribute('data-key', 'e:' + p.e); layer.append(g); fitMark(g, el, 'pin', taken);
  }
  if (run.preview) for (const m of blastMarks(groups, has)) {  // before Play: whose clients may see a change (declared, not measured)
    const g = blastEl(runPartEl(m.id), m.label, taken); if (g) { g.setAttribute('data-key', 'x:' + m.id); g.setAttribute('data-id', m.id); layer.append(g); }
  }
  if (keep) layer.querySelector(`[data-key="${attr(keep)}"]`)?.focus();
  ringStep();
}

// The ring: the chosen step's parts (or the current step's) get an accent outline. The blast radius keeps its own red dashes.
// Before Play every part the scenario will touch also gets a dotted ring (the ghost outline).
function ringStep() {
  const f = $('flow'); if (!f) return;
  f.querySelectorAll('[data-runring], [data-runghost], [data-runblast]').forEach(e => { for (const a of ['data-runring', 'data-runghost', 'data-runblast']) e.removeAttribute(a); });
  const run = f.dataset.cv ? shownRun() : null;
  if (run && run.preview) for (const id of ghostParts(groups, has)) runPartEl(id)?.setAttribute('data-runghost', '1');
  const g = run ? focusStep(groups, S.runSel) : null;
  if (g && run.preview) for (const id of g.blast || []) runPartEl(id)?.setAttribute('data-runblast', '1');  // what this step may affect: the blast radius's red dashes
  if (g) for (const id of g.ids) runPartEl(id)?.setAttribute('data-runring', '1');
  f.querySelectorAll('#runlayer .rl-badge').forEach(b => b.classList.toggle('sel', !!g && +b.dataset.n === g.n));
}

// ---- the rail (inspector, Run tab) and the phone sheet ----
function paintRunRail(run) {
  const pane = $('ipane-run'); if (!pane) return;
  pane.toggleAttribute('data-run', !!run);
  $('it-run').textContent = run && run.running ? 'Run ●' : 'Run';
  const last = lastRun();
  $('run-last').hidden = !!run || !last;
  if (last && !run) $('run-lastbtn').textContent = `Replay the last run: ${last.name}, ${runVerdict(last)}`;
  if (!run) {
    $('run-head').textContent = 'No scenario run to show';
    $('run-none').hidden = false; $('runrail').innerHTML = ''; $('runpins').innerHTML = ''; $('run-tools').hidden = true; $('run-cvnote').hidden = true; $('run-sum').hidden = true; return;
  }
  $('run-none').hidden = true; $('run-tools').hidden = !!run.running;
  $('run-replay').hidden = !replayable(run); $('run-replay').textContent = S.replay ? 'End replay' : 'Replay the run';
  $('run-head').textContent = S.replay ? replayHeading(run, groups, stops, S.replay.at) : runHeading(run, groups);
  $('run-sum').hidden = !run.preview && !fromCli(run);
  $('run-sum').textContent = run.preview ? `${previewSummary(groups, pins, has)}. Dashed badges: the steps that will act on a part; ◎: its clients may see a change (declared, not measured).` : cliNote(run);
  $('run-clear').textContent = run.preview ? 'Take the preview off the diagram' : 'Take off the diagram';
  $('run-cvnote').hidden = !!S.circuit;
  const chosen = focusStep(groups, S.runSel);
  $('runrail').innerHTML = groups.map(g => {
    const r = railRow(g, has, ENT_NAME), sel = chosen && chosen.n === g.n;
    return `<li class="rr k-${r.state}${r.current ? ' cur' : ''}"><button type="button" class="rrbtn" data-n="${r.n}" aria-pressed="${sel}"${r.current ? ' aria-current="step"' : ''}>
      <span class="rb k-${r.state}" aria-hidden="true">${r.n} ${r.glyph}</span><span class="rtx"><span class="rtop"><b>${esc(r.text)}</b></span>
      <span class="small dim">${esc([r.ran || r.at, r.where, r.extra].filter(Boolean).join(' · '))}</span>
      <span class="rword">Step ${r.n}: ${esc(r.word)}</span>${r.detail && r.state !== 'done' && r.state !== 'now' ? `<span class="small dim">${esc(r.detail)}</span>` : ''}${run.preview && r.blast ? `<span class="small dim"><span aria-hidden="true">◎</span> ${esc(r.blast)}</span>` : ''}${r.off ? `<span class="small roff">${esc(r.off)}</span>` : ''}</span></button></li>`;
  }).join('');
  $('runrail').querySelectorAll('.rrbtn').forEach(b => { b.onclick = () => chooseStep(+b.dataset.n, false); });
  $('runpins').innerHTML = pins.length ? pins.map(p => `<li><button type="button" class="rrbtn rpin" data-e="${p.e}"><span class="rb k-${p.state}" aria-hidden="true">E${p.e} ${p.glyph}</span><span class="rtx"><b>${esc(p.label)}</b><span class="rword">Expectation E${p.e}: ${esc(p.word)}${p.t != null ? ` at ${Math.round(p.t)} s` : ''}</span>${p.ids.some(has) ? '' : `<span class="small roff">Not on the diagram: ${esc(p.ids.map(id => partWords(id, ENT_NAME)).join(', ') || 'no part')}${p.ids.length ? ' not in this cluster' : ''}.</span>`}</span></button></li>`).join('') : '';
  $('runpins').querySelectorAll('.rpin').forEach(b => { b.onclick = () => { const p = pins.find(x => x.e === +b.dataset.e), id = p && p.ids.find(has); if (id) { S.runSel = null; flashPart(id); } }; });
}

function flashPart(id) {  // show a part the rail points at: scroll it into view (no animation under reduced motion)
  const el = runPartEl(id); if (el && phone()) el.scrollIntoView({ block: 'nearest', inline: 'center', behavior: reduced.matches ? 'auto' : 'smooth' });
}

function paintRunSheet(run) {
  const sh = $('runsheet'); if (!sh) return;
  const g = run ? focusStep(groups, S.runSel) : null, s = sheetFor(groups, g, ENT_NAME);
  sh.hidden = !s || (S.runSheetOff === runKey(run) && !S.replay);
  if (!s) return;
  $('rs-badge').className = `rb k-${g.state}`; $('rs-badge').textContent = `${s.n} ${s.glyph}`;
  $('rs-title').textContent = s.title; $('rs-line').textContent = fromCli(run) ? `Run from the CLI · ${s.line}` : s.line;
  $('rs-prev').disabled = s.prev == null; $('rs-prev').textContent = s.prev == null ? '◀' : `◀ ${s.prev}`; $('rs-prev').setAttribute('aria-label', s.prev == null ? 'No previous step' : `Previous step, ${s.prev}`);
  $('rs-next').disabled = s.next == null; $('rs-next').textContent = s.next == null ? '▶' : `${s.next} ▶`; $('rs-next').setAttribute('aria-label', s.next == null ? 'No next step' : `Next step, ${s.next}`);
  $('rs-steps').textContent = `Steps ${s.total}`;
  paintSlider(run);
}

// ---- replay: the player strip (a WAI-ARIA slider over the recorded steps) ----
const lastRun = () => { const r = S.trLast; return replayable(r) && S.runHidden === runKey(r) ? r : null; };  // the run Stop (or Take off) took off the diagram
function paintSlider(run) {
  const box = $('rs-replay'), on = !!(run && S.replay && stops.length);
  box.hidden = !on; $('runsheet').classList.toggle('replaying', on);
  if (!on) { stopPlay(); return; }
  const at = S.replay.at, sl = $('rs-slider'), key = stops.map(x => x.n).join(',');
  sl.setAttribute('aria-valuemax', String(stops.length)); sl.setAttribute('aria-valuenow', String(at + 1)); sl.setAttribute('aria-valuetext', replayText(recorded, stops, at));
  $('rs-rcap').textContent = replayCaption(recorded, stops);
  if (sl.dataset.key !== key) {
    sl.dataset.key = key; sl.classList.toggle('dense', stops.length > 12);
    $('rs-ticks').innerHTML = stops.map((x, i) => `<span class="rs-tick" data-i="${i}">${x.n}</span>`).join('');
  }
  $('rs-ticks').querySelectorAll('.rs-tick').forEach((t, i) => { t.dataset.at = i < at ? 'past' : i === at ? 'now' : 'next'; });
  $('rs-play').hidden = reduced.matches;  // reduced motion: no playback; the slider changes states in place
}
function stopPlay() {
  clearInterval(playTimer); playTimer = null;
  const b = $('rs-play'); if (b) b.textContent = '▶ Play';
}
function togglePlay() {
  if (playTimer || !S.replay || reduced.matches) { stopPlay(); return; }
  if (S.replay.at >= stops.length - 1) replayTo(0);
  playTimer = setInterval(() => { if (!S.replay || S.replay.at >= stops.length - 1) { stopPlay(); announce('Replay reached the last recorded step'); return; } S.replay.at++; paintRun(); }, 1200);
  $('rs-play').textContent = '⏸ Pause';
}
function replayTo(at) { if (!S.replay) return; S.replay.at = replayClamp(at, stops); S.runSel = null; paintRun(); }

// Replay the run on the diagram, from its first recorded step: the last run after Stop, or the run shown now.
export function startReplay() {
  const r = S.trLast && S.trLast.name ? S.trLast : null; if (!replayable(r)) return;
  S.runHidden = ''; S.runSheetOff = null; S.runSel = null; S.replay = { key: runKey(r), at: 0 };
  if (!S.circuit) setCircuit(true);
  showPane('run'); paintRun(); paintRunLayer(true);
  $('trafficpanel').scrollIntoView({ block: 'start', behavior: 'auto' });  // the diagram and the strip over it, wherever the button was
  $('rs-slider').focus({ preventScroll: true });
  if (phone()) flashPart(focusStep(groups, S.runSel)?.ids.find(has));
}
export function endReplay() {  // back to the run as it ended
  stopPlay(); S.replay = null; S.runSel = null; paintRun();
  $('run-head')?.focus({ preventScroll: true }); announce('Replay ended: the badges show the run as it ended');
}

// Pick a step from a badge, the rail or the sheet: ring its parts, mark it in the rail, and say it.
export function chooseStep(n, fromBadge) {
  const i = S.replay ? stops.findIndex(x => x.n === n) : -1;
  if (i >= 0) { stopPlay(); S.replay.at = i; S.runSel = null; paintRun(); } else S.runSel = n;  // in replay a recorded step moves the slider
  const g = groups.find(x => x.n === n);
  ringStep(); paintRunRail(shownRun()); paintRunSheet(shownRun());
  if (fromBadge) { showPane('run'); $('runrail').querySelector(`[data-n="${n}"]`)?.scrollIntoView({ block: 'nearest' }); }
  if (g) { if (!fromBadge) flashPart(g.ids.find(has)); announce(`Step ${g.n}: ${g.text}, ${RUN_WORD[g.state] || g.state}`); }
}

// Everything, from the run state the page just polled (scenarios.js) or after the drawing was rebuilt (circuit.js).
export function paintRun() {
  const r = S.trLast;  // Stop pressed on this page: once the run says it stopped, the diagram goes back to plain (Watch on the diagram brings it back)
  if (S.runStopHide && r && r.name === S.runStopHide && !r.running) { S.runHidden = runKey(r); S.runStopHide = ''; S.runSel = null; }
  const run = shownRun();
  groups = recorded = runGroups(run); pins = run ? expectPins(run) : [];
  if (S.replay && !(replayable(run) && S.replay.key === runKey(run))) { stopPlay(); S.replay = null; }  // a new run, or this one taken off: replay ends
  stops = S.replay ? replayStops(recorded) : [];
  if (S.replay && stops.length) {
    const at = S.replay.at = replayClamp(S.replay.at, stops);
    groups = replayGroups(recorded, stops, at); pins = replayPins(pins, stops, at);
    if (S.runSel == null || stops.some(x => x.n === S.runSel)) S.runSel = stops[at].n;  // the ring and the rail follow the slider
  }
  if (S.runSel != null && !groups.some(g => g.n === S.runSel)) S.runSel = null;
  const cur = groups.find(g => g.current) || null;
  if (run && run.running && cur && cur.n !== lastCur) {  // a new step: follow it (unless the user picked one), and say it once
    lastCur = cur.n; if (S.runSel != null && S.runSel < cur.n) S.runSel = null;
    if (!$('tab-nodes').hidden) { announce(`Step ${cur.n} of ${groups.length}: ${cur.text}`); flashPart(cur.ids.find(has)); }
  }
  if (!run || !run.running) lastCur = null;
  paintRunLayer(); paintRunRail(run); paintRunSheet(run);
}

// "Watch on the diagram" (decision 5: Play keeps the user where they are; this is the one click to the map).
// "Show on the diagram" before Play is the same click: the picked scenario's preview (phase 2).
export function watchRun() {
  S.runHidden = '';  // asked for: show it again even if it was taken off
  if (!S.circuit) setCircuit(true);
  zoomTo('cluster'); selectTab('nodes');
  S.runSheetOff = null; showPane('run'); paintRun(); paintRunLayer(true);
  $('run-head')?.focus({ preventScroll: true });  // the rail is next for a keyboard; the eye goes to the diagram and the step above it
  $('trafficpanel').scrollIntoView({ block: 'start', behavior: 'auto' });
  const g = focusStep(groups, S.runSel); if (g) flashPart(g.ids.find(has));
}

export function clearRun() {  // take a finished run (or a preview) off the diagram; the Test run tab and the report keep it
  const r = shownRun(); if (r) S.runHidden = runKey(r);
  stopPlay(); S.replay = null; S.runSel = null; paintRun(); announce(r && r.preview ? 'The preview was taken off the diagram' : 'The run was taken off the diagram');
}

export function init() {
  $('it-run').onclick = () => { showPane('run'); paintRun(); };
  $('run-clear').onclick = clearRun;
  $('run-tocv').onclick = watchRun;
  $('run-open').onclick = () => selectTab('run', true);
  $('rs-prev').onclick = () => { const s = sheetFor(groups, focusStep(groups, S.runSel)); if (s && s.prev != null) chooseStep(s.prev, false); };
  $('rs-next').onclick = () => { const s = sheetFor(groups, focusStep(groups, S.runSel)); if (s && s.next != null) chooseStep(s.next, false); };
  $('rs-steps').onclick = () => { showPane('run'); $('ipane-run').scrollIntoView({ block: 'start', behavior: reduced.matches ? 'auto' : 'smooth' }); $('runrail').querySelector('[aria-pressed=true]')?.focus({ preventScroll: true }); };
  $('rs-x').onclick = () => { const r = shownRun(); if (r) S.runSheetOff = runKey(r); if (S.replay) endReplay(); else paintRunSheet(r); };
  matchMedia('(max-width:640px)').addEventListener('change', () => paintRunSheet(shownRun()));
  $('run-replay').onclick = () => (S.replay ? endReplay() : startReplay());
  $('run-lastbtn').onclick = startReplay;
  $('rs-end').onclick = endReplay;
  $('rs-play').onclick = togglePlay;
  reduced.addEventListener('change', () => { if (reduced.matches) stopPlay(); paintRunSheet(shownRun()); });
  const sl = $('rs-slider');
  sl.addEventListener('keydown', e => {
    const to = S.replay ? replayKey(e.key, S.replay.at, stops.length) : null; if (to == null) return;
    e.preventDefault(); stopPlay(); replayTo(to);
  });
  const fromX = x => {  // the nearest tick to a pointer
    const ts = [...$('rs-ticks').children]; if (!ts.length) return 0;
    return ts.reduce((best, t, i) => { const b = t.getBoundingClientRect(), d = Math.abs(b.left + b.width / 2 - x); return d < best.d ? { i, d } : best; }, { i: 0, d: Infinity }).i;
  };
  sl.addEventListener('pointerdown', e => { if (!S.replay) return; stopPlay(); sl.setPointerCapture?.(e.pointerId); sl.focus({ preventScroll: true }); replayTo(fromX(e.clientX)); });
  sl.addEventListener('pointermove', e => { if (S.replay && sl.hasPointerCapture?.(e.pointerId)) { const i = fromX(e.clientX); if (i !== S.replay.at) replayTo(i); } });
}

