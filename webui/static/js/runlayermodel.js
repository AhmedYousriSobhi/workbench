// The run layer's logic (ADR 0086, phase 1), with no DOM (webui/tests/js/runlayermodel.test.mjs); runlayer.js draws it.
// A scenario run, as GET /api/scenarios/run reports it, becomes: the scenario's own steps (grouped by their YAML step number,
// so fault-model and ramp expansions sit under the step that made them), one badge per diagram part, one pin per expectation,
// and the words for the step rail and the phone sheet. Every state is a glyph and a word, never a colour alone.
import { describe } from './testrunmodel.js';

export const RUN_GLYPH = { done: '✓', now: '●', active: '◐', next: '○', cli: '↷', skipped: '↷', failed: '✗', notrun: '○' };
export const RUN_WORD = { done: 'done', now: 'now', active: 'in progress', next: 'next', cli: 'CLI only', skipped: 'skipped', failed: 'failed', notrun: 'not run' };
const CLI = /host CLI|need the host/;

function rowState(r) {
  if (r.status === 'failed') return 'failed';
  if (r.status === 'ok') return 'done';
  if (r.status === 'skipped') return r.page === false || CLI.test(r.detail || '') ? 'cli' : 'skipped';
  return 'pending';
}

// The scenario's steps, in order: [{n, at, t, ids, noPart, text, target, state, current, rows}]. `current` marks the step the run is at
// (the step of the newest action applied, in time order, while the run plays); its badge says "now" unless it failed or was skipped.
// A step whose expansions are partly applied (a temperature ramp runs beside later steps) is "in progress".
export function runGroups(run) {
  if (!run || !Array.isArray(run.steps)) return [];
  const by = new Map();
  let lastN = null;
  run.steps.forEach((r, i) => {
    if (r.status && r.status !== 'pending') lastN = r.n || i + 1;
    const n = r.n || i + 1;
    if (!by.has(n)) by.set(n, { n, at: r.at, t: null, ids: [], noPart: '', text: describe(r), target: r.target, rows: [] });
    const g = by.get(n);
    g.rows.push({ ...r, state: rowState(r) });
    for (const id of r.ids || []) if (!g.ids.includes(id)) g.ids.push(id);
    if (!g.noPart && !(r.ids || []).length && r.no_part) g.noPart = r.no_part;
    if (r.t != null) g.t = g.t == null ? r.t : Math.min(g.t, r.t);
  });
  const groups = [...by.values()].sort((a, b) => a.n - b.n);
  for (const g of groups) {
    const st = g.rows.map(r => r.state);
    g.state = st.includes('failed') ? 'failed' : st.every(s => s === 'pending') ? (run.running ? 'next' : 'notrun')
      : st.includes('pending') ? 'active' : st.every(s => s === 'cli') ? 'cli' : st.every(s => s === 'skipped' || s === 'cli') ? 'skipped' : 'done';
    g.current = false;
  }
  const cur = run.running && lastN != null ? groups.find(g => g.n === lastN) : null;
  if (cur) { cur.current = true; if (cur.state === 'done' || cur.state === 'active') cur.state = 'now'; }
  return groups;
}

export const glyphOf = state => RUN_GLYPH[state] || '○';
export const wordOf = state => RUN_WORD[state] || state;
const reachedState = s => s !== 'next' && s !== 'notrun';

// One badge per part: the newest step that reached it (or, before any did, the first that will), plus how many other steps touch it.
// `has(id)` says whether the diagram draws that part now. Sorted by step number, so Tab follows the scenario.
export function partBadges(groups, has = () => true) {
  const by = new Map(), total = groups.length;
  for (const g of groups) for (const id of g.ids) if (has(id)) (by.get(id) || by.set(id, []).get(id)).push(g);
  const out = [];
  for (const [id, gs] of by) {
    const reached = gs.filter(g => reachedState(g.state)), shown = reached.length ? reached.at(-1) : gs[0], others = gs.filter(g => g !== shown).map(g => g.n);
    out.push({ id, n: shown.n, state: shown.state, glyph: glyphOf(shown.state), current: shown.current, more: others.length, others,
      label: `Step ${shown.n} of ${total}, ${wordOf(shown.state)}: ${shown.text}.${others.length ? ` Also step${others.length === 1 ? '' : 's'} ${others.join(', ')} on this part.` : ''}` });
  }
  return out.sort((a, b) => a.n - b.n || a.id.localeCompare(b.id));
}

// The expectations: E1, E2 ... from the planned list, each with its result once it is known.
export function expectPins(run) {
  const plan = (run && run.plan_expects) || [], got = new Map(((run && run.expects) || []).map(x => [x.label, x]));
  return plan.map((p, i) => {
    const r = got.get(p.label), state = r ? { ok: 'done', failed: 'failed', skipped: 'cli' }[r.status] || 'skipped' : p.page === false ? 'cli' : run.running ? 'next' : 'notrun';
    const word = { done: 'held', failed: 'did not hold', cli: 'CLI only', next: 'waiting', notrun: 'not checked' }[state] || wordOf(state);
    return { e: p.e || i + 1, label: p.label, ids: p.ids || [], state, glyph: glyphOf(state), word, t: r && r.t != null ? r.t : null,
      aria: `Expectation E${p.e || i + 1}, ${word}: ${p.label}` };
  });
}

// Why a step has no badge: the engine's reason, or the parts it names are not drawn in this cluster. '' when it has a badge.
export function offDiagram(g, has = () => true) {
  if (!g.ids.length) return g.noPart || 'it names no part of the diagram';
  const shown = g.ids.filter(has);
  return shown.length ? '' : `${g.ids.join(', ')} ${g.ids.length === 1 ? 'is' : 'are'} not in this cluster`;
}

const secs = v => v == null ? '' : `${Math.round(v)} s`;
const onParts = (g, nameOf) => g.ids.length ? `on ${g.ids.map(nameOf).join(', ')}` : '';
// One rail row: the number and glyph, the time it is planned at (and when it ran), the words, the parts it touched, and the state.
export function railRow(g, has = () => true, nameOf = id => id) {
  const off = offDiagram(g, has), extra = g.rows.length > 1 ? `${g.rows.length} actions` : '';
  return { n: g.n, state: g.state, glyph: glyphOf(g.state), word: wordOf(g.state), current: g.current, at: `at ${secs(g.at)}`, ran: g.t == null ? '' : `ran at ${secs(g.t)}`,
    text: g.text, where: onParts(g, nameOf), extra, off: off ? `Not on the diagram: ${off}.` : '',
    detail: g.rows.map(r => r.detail).filter(Boolean)[0] || '' };
}

// The rail's heading: name, the run's state in words, the step it is at.
export function runHeading(run, groups) {
  if (!run || !run.name) return '';
  const cur = groups.find(g => g.current), done = groups.filter(g => reachedState(g.state)).length;
  const word = run.running ? `Running · step ${cur ? cur.n : 0} of ${groups.length}` : run.stopped ? `Stopped after step ${done} of ${groups.length}`
    : run.ok === false ? 'Failed' : groups.some(g => g.state === 'cli' || g.state === 'skipped') ? 'Passed, with skipped steps' : 'Passed';
  return `${run.name} · ${word}`;
}

// The step the phone sheet and the ring show: the chosen one if it still exists, else the current one, else the last reached, else the first.
export function focusStep(groups, chosen) {
  return groups.find(g => g.n === chosen) || groups.find(g => g.current) || groups.filter(g => reachedState(g.state)).at(-1) || groups[0] || null;
}

// The phone sheet's words for one step, and the step numbers its Previous and Next buttons go to.
export function sheetFor(groups, g, nameOf = id => id) {
  if (!g) return null;
  const i = groups.indexOf(g), prev = groups[i - 1], next = groups[i + 1];
  return { n: g.n, glyph: glyphOf(g.state), title: `Step ${g.n}: ${g.text}`, line: [g.t == null ? `at ${secs(g.at)}` : `ran at ${secs(g.t)}`, onParts(g, nameOf), wordOf(g.state)].filter(Boolean).join(' · '),
    prev: prev ? prev.n : null, next: next ? next.n : null, total: groups.length };
}

// A key that changes when anything the layer draws changes, so a poll that brings nothing new redraws nothing.
export function runSig(run) {
  if (!run || !run.name) return '';
  return JSON.stringify([run.name, run.started, run.running, run.stopped, run.ok, (run.steps || []).map(s => s.status), (run.expects || []).map(x => x.status)]);
}
