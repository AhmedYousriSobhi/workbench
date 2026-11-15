// The run layer (ADR 0086, phase 1): a scenario run drawn on the Circuit view. runlayermodel.js decides what to show; this file draws
// it: numbered badges on the parts each step touched (a number and a glyph, never colour alone), a ring on the chosen step's parts,
// expectation pins, the step rail in the inspector's Run tab, and on a phone a bottom sheet with Previous and Next.
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
import { RUN_WORD, expectPins, focusStep, partBadges, railRow, runGroups, runHeading, runSig, sheetFor } from './runlayermodel.js';

const BH = 36;  // badge height in drawing units (the Circuit view is 1700 wide)
let drawnSig = '', lastCur = null, groups = [], pins = [];
const attr = s => String(s).replace(/["\\]/g, '\\$&');
const phone = () => matchMedia('(max-width:640px)').matches;

// The run the layer shows: the last one the page polled, unless the user cleared it from the diagram.
export function shownRun() { const r = S.trLast; return r && r.name && S.runHidden !== `${r.name}@${r.started}` ? r : null; }

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
  taken.push({ x, y, w });
  return { x, y };
}

function markEl(cls, text, more, aria, n, onAct) {
  const g = sv('g', { class: cls, role: 'button', tabindex: '0', 'aria-label': aria, 'data-n': n });
  g.append(sv('title', {}, aria), sv('rect', { height: BH, rx: BH / 2 }), sv('text', { class: 'rl-t' }, text));
  if (more) g.append(sv('text', { class: 'rl-more' }, more));
  g.addEventListener('click', e => { e.stopPropagation(); onAct(); });
  g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onAct(); } });
  return g;
}
function fitMark(g, el, kind, taken) {  // size the pill to its text, then place it
  const [t, m] = [g.querySelector('.rl-t'), g.querySelector('.rl-more')], tw = t.getComputedTextLength(), mw = m ? m.getComputedTextLength() + 8 : 0, w = Math.ceil(tw + mw + 26);
  const { x, y } = placeMark(el, w, BH, kind, taken), r = g.querySelector('rect');
  r.setAttribute('x', x); r.setAttribute('y', y); r.setAttribute('width', w);
  t.setAttribute('x', x + 13); t.setAttribute('y', y + 25);
  if (m) { m.setAttribute('x', x + 13 + tw + 8); m.setAttribute('y', y + 24); }
}

export function paintRunLayer(force) {
  const f = $('flow'), run = shownRun();
  if (!f) return;
  const sig = f.dataset.cv && run ? runSig(run) + '|' + (f.querySelector('.cv-box') ? 'b' : '') : '';
  if (!force && sig === drawnSig && (!sig || f.querySelector('#runlayer'))) { ringStep(); return; }
  drawnSig = sig;
  const keep = document.activeElement && document.activeElement.closest?.('#runlayer') ? document.activeElement.getAttribute('data-key') : null;
  f.querySelector('#runlayer')?.remove();
  if (!sig) { ringStep(); return; }
  const layer = sv('g', { id: 'runlayer', class: 'runlayer', role: 'group', 'aria-label': `Scenario ${run.name}: step badges, in step order` }), taken = [];
  f.append(layer);
  for (const b of partBadges(groups, has)) {  // in step order, so Tab follows the scenario
    const el = runPartEl(b.id), g = markEl(`rl-badge k-${b.state}${b.current ? ' cur' : ''}`, `${b.n} ${b.glyph}`, b.more ? `+${b.more}` : '', b.label, b.n, () => chooseStep(b.n, true));
    g.setAttribute('data-key', 'b:' + b.id); g.setAttribute('data-id', b.id); layer.append(g); fitMark(g, el, 'badge', taken);
  }
  for (const p of pins) {
    const el = p.ids.map(runPartEl).find(Boolean); if (!el) continue;
    const g = markEl(`rl-pin k-${p.state}`, `E${p.e} ${p.glyph}`, '', p.aria, '', () => { showPane('run'); $('ipane-run')?.querySelector(`[data-e="${p.e}"]`)?.focus(); });
    g.setAttribute('data-key', 'e:' + p.e); layer.append(g); fitMark(g, el, 'pin', taken);
  }
  if (keep) layer.querySelector(`[data-key="${attr(keep)}"]`)?.focus();
  ringStep();
}

// The ring: the chosen step's parts (or the current step's) get an accent outline. The blast radius keeps its own red dashes.
function ringStep() {
  const f = $('flow'); if (!f) return;
  f.querySelectorAll('[data-runring]').forEach(e => e.removeAttribute('data-runring'));
  const g = shownRun() && f.dataset.cv ? focusStep(groups, S.runSel) : null;
  if (g) for (const id of g.ids) runPartEl(id)?.setAttribute('data-runring', '1');
  f.querySelectorAll('#runlayer .rl-badge').forEach(b => b.classList.toggle('sel', !!g && +b.dataset.n === g.n));
}

// ---- the rail (inspector, Run tab) and the phone sheet ----
function paintRunRail(run) {
  const pane = $('ipane-run'); if (!pane) return;
  pane.toggleAttribute('data-run', !!run);
  $('it-run').textContent = run && run.running ? 'Run ●' : 'Run';
  if (!run) {
    $('run-head').textContent = 'No scenario run to show';
    $('run-none').hidden = false; $('runrail').innerHTML = ''; $('runpins').innerHTML = ''; $('run-tools').hidden = true; $('run-cvnote').hidden = true; return;
  }
  $('run-none').hidden = true; $('run-tools').hidden = !!run.running;
  $('run-head').textContent = runHeading(run, groups);
  $('run-cvnote').hidden = !!S.circuit;
  const chosen = focusStep(groups, S.runSel);
  $('runrail').innerHTML = groups.map(g => {
    const r = railRow(g, has, ENT_NAME), sel = chosen && chosen.n === g.n;
    return `<li class="rr k-${r.state}${r.current ? ' cur' : ''}"><button type="button" class="rrbtn" data-n="${r.n}" aria-pressed="${sel}"${r.current ? ' aria-current="step"' : ''}>
      <span class="rb k-${r.state}" aria-hidden="true">${r.n} ${r.glyph}</span><span class="rtx"><span class="rtop"><b>${esc(r.text)}</b></span>
      <span class="small dim">${esc([r.ran || r.at, r.where, r.extra].filter(Boolean).join(' · '))}</span>
      <span class="rword">Step ${r.n}: ${esc(r.word)}</span>${r.detail && r.state !== 'done' && r.state !== 'now' ? `<span class="small dim">${esc(r.detail)}</span>` : ''}${r.off ? `<span class="small roff">${esc(r.off)}</span>` : ''}</span></button></li>`;
  }).join('');
  $('runrail').querySelectorAll('.rrbtn').forEach(b => { b.onclick = () => chooseStep(+b.dataset.n, false); });
  $('runpins').innerHTML = pins.length ? pins.map(p => `<li><button type="button" class="rrbtn rpin" data-e="${p.e}"><span class="rb k-${p.state}" aria-hidden="true">E${p.e} ${p.glyph}</span><span class="rtx"><b>${esc(p.label)}</b><span class="rword">Expectation E${p.e}: ${esc(p.word)}${p.t != null ? ` at ${Math.round(p.t)} s` : ''}</span>${p.ids.some(has) ? '' : `<span class="small roff">Not on the diagram: ${esc(p.ids.join(', ') || 'no part')}${p.ids.length ? ' not in this cluster' : ''}.</span>`}</span></button></li>`).join('') : '';
  $('runpins').querySelectorAll('.rpin').forEach(b => { b.onclick = () => { const p = pins.find(x => x.e === +b.dataset.e), id = p && p.ids.find(has); if (id) { S.runSel = null; flashPart(id); } }; });
}

function flashPart(id) {  // show a part the rail points at: scroll it into view (no animation under reduced motion)
  const el = runPartEl(id); if (el && phone()) el.scrollIntoView({ block: 'nearest', inline: 'center', behavior: reduced.matches ? 'auto' : 'smooth' });
}

function paintRunSheet(run) {
  const sh = $('runsheet'); if (!sh) return;
  const g = run ? focusStep(groups, S.runSel) : null, s = sheetFor(groups, g, ENT_NAME);
  sh.hidden = !s || S.runSheetOff === `${run.name}@${run.started}`;
  if (!s) return;
  $('rs-badge').className = `rb k-${g.state}`; $('rs-badge').textContent = `${s.n} ${s.glyph}`;
  $('rs-title').textContent = s.title; $('rs-line').textContent = s.line;
  $('rs-prev').disabled = s.prev == null; $('rs-prev').textContent = s.prev == null ? '◀' : `◀ ${s.prev}`; $('rs-prev').setAttribute('aria-label', s.prev == null ? 'No previous step' : `Previous step, ${s.prev}`);
  $('rs-next').disabled = s.next == null; $('rs-next').textContent = s.next == null ? '▶' : `${s.next} ▶`; $('rs-next').setAttribute('aria-label', s.next == null ? 'No next step' : `Next step, ${s.next}`);
  $('rs-steps').textContent = `Steps ${s.total}`;
}

// Pick a step from a badge, the rail or the sheet: ring its parts, mark it in the rail, and say it.
export function chooseStep(n, fromBadge) {
  S.runSel = n; const g = groups.find(x => x.n === n);
  ringStep(); paintRunRail(shownRun()); paintRunSheet(shownRun());
  if (fromBadge) { showPane('run'); $('runrail').querySelector(`[data-n="${n}"]`)?.scrollIntoView({ block: 'nearest' }); }
  if (g) { if (!fromBadge) flashPart(g.ids.find(has)); announce(`Step ${g.n}: ${g.text}, ${RUN_WORD[g.state] || g.state}`); }
}

// Everything, from the run state the page just polled (scenarios.js) or after the drawing was rebuilt (circuit.js).
export function paintRun(run = shownRun()) {
  groups = runGroups(run); pins = run ? expectPins(run) : [];
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
export function watchRun() {
  if (!S.circuit) setCircuit(true);
  zoomTo('cluster'); selectTab('nodes');
  S.runSheetOff = null; showPane('run'); paintRun(); paintRunLayer(true);
  $('run-head')?.focus({ preventScroll: true });  // the rail is next for a keyboard; the eye goes to the diagram and the step above it
  $('trafficpanel').scrollIntoView({ block: 'start', behavior: 'auto' });
  const g = focusStep(groups, S.runSel); if (g) flashPart(g.ids.find(has));
}

export function clearRun() {  // take a finished run off the diagram; the Test run tab and the report keep it
  const r = S.trLast; if (r && r.name) S.runHidden = `${r.name}@${r.started}`;
  S.runSel = null; paintRun(); announce('The run was taken off the diagram');
}

export function init() {
  $('it-run').onclick = () => { showPane('run'); paintRun(); };
  $('run-clear').onclick = clearRun;
  $('run-tocv').onclick = watchRun;
  $('run-open').onclick = () => selectTab('run', true);
  $('rs-prev').onclick = () => { const s = sheetFor(groups, focusStep(groups, S.runSel)); if (s && s.prev != null) chooseStep(s.prev, false); };
  $('rs-next').onclick = () => { const s = sheetFor(groups, focusStep(groups, S.runSel)); if (s && s.next != null) chooseStep(s.next, false); };
  $('rs-steps').onclick = () => { showPane('run'); $('ipane-run').scrollIntoView({ block: 'start', behavior: reduced.matches ? 'auto' : 'smooth' }); $('runrail').querySelector('[aria-pressed=true]')?.focus({ preventScroll: true }); };
  $('rs-x').onclick = () => { const r = shownRun(); if (r) S.runSheetOff = `${r.name}@${r.started}`; paintRunSheet(r); };
  matchMedia('(max-width:640px)').addEventListener('change', () => paintRunSheet(shownRun()));
}

