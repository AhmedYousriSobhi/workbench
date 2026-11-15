// The run layer's logic (ADR 0086): steps grouped by their YAML number, one badge per part, state glyphs and words, Tab order,
// expectation pins and the phone sheet's words. runlayer.js draws what these return.
import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { RUN_GLYPH, RUN_WORD, expectPins, focusStep, offDiagram, partBadges, railRow, runGroups, runHeading, runSig, sheetFor } from '../../static/js/runlayermodel.js';

const row = (n, at, target, ids, status = 'pending', extra = {}) => ({ n, at, target, ids, status, detail: '', page: true, t: status === 'pending' ? null : at, do: { nodes: ['cn002'], power: 'Off' }, ...extra });
// node-down at step 3 of 6, as the page polls it
const nodeDown = (statuses, running = true) => ({
  name: 'node-down', running, started: 1000, ok: running ? null : true, stopped: false,
  steps: [row(1, 0, 'bmc-farm', ['bmc-farm', 'cn002']), row(2, 0, 'bcm-mock', ['bcm-mock', 'cn002'], 'pending', { do: { nodes: ['cn002'], state: 'DOWN' } }),
    row(3, 5, 'slurm', ['slurmctld', 'cn002'], 'pending', { do: { drain: ['cn002'], reason: 'node down' } }), row(4, 40, 'bmc-farm', ['bmc-farm', 'cn002'], 'pending', { do: { nodes: ['cn002'], clear: true } }),
    row(5, 40, 'bcm-mock', ['bcm-mock', 'cn002'], 'pending', { do: { nodes: ['cn002'], state: 'UP' } }), row(6, 40, 'slurm', ['slurmctld', 'cn002'], 'pending', { do: { resume: ['cn002'] } })]
    .map((r, i) => ({ ...r, status: statuses[i] || 'pending', t: statuses[i] && statuses[i] !== 'pending' ? r.at : null })),
  expects: [], plan_expects: [{ e: 1, label: 'slurm.node_state {"node": "cn002", "state": "drain"}', ids: ['cn002'], page: false }],
});

test('every state has a glyph and a word, and no two states share both', () => {
  for (const k of Object.keys(RUN_GLYPH)) assert.ok(RUN_WORD[k], k);
  assert.deepEqual([RUN_GLYPH.done, RUN_GLYPH.now, RUN_GLYPH.next, RUN_GLYPH.cli, RUN_GLYPH.failed], ['✓', '●', '○', '↷', '✗']);
  const pairs = Object.keys(RUN_GLYPH).map(k => RUN_GLYPH[k] + RUN_WORD[k]);
  assert.equal(new Set(pairs).size, pairs.length);
});

test('the current step is the newest one applied; the ones before are done, the rest next', () => {
  const g = runGroups(nodeDown(['ok', 'ok', 'ok']));
  assert.deepEqual(g.map(x => x.state), ['done', 'done', 'now', 'next', 'next', 'next']);
  assert.deepEqual(g.map(x => x.current), [false, false, true, false, false, false]);
  assert.equal(g[2].text, 'Drain cn002 in Slurm (node down)');
  assert.equal(runHeading(nodeDown(['ok', 'ok', 'ok']), g), 'node-down · Running · step 3 of 6');
});

test('a finished run has no current step; a stopped one says where it stopped and what never ran', () => {
  const done = runGroups(nodeDown(['ok', 'ok', 'ok', 'ok', 'ok', 'ok'], false));
  assert.ok(done.every(x => x.state === 'done' && !x.current));
  const stopped = { ...nodeDown(['ok', 'ok'], false), stopped: true, ok: true }, g = runGroups(stopped);
  assert.deepEqual(g.map(x => x.state), ['done', 'done', 'notrun', 'notrun', 'notrun', 'notrun']);
  assert.equal(runHeading(stopped, g), 'node-down · Stopped after step 2 of 6');
});

test('a CLI-only skip and a failure keep their own state, never a pass', () => {
  const run = nodeDown(['ok', 'failed', 'skipped'], false);
  run.steps[2].page = false; run.steps[2].detail = 'slurm steps need the host CLI (no Docker access here)'; run.ok = false;
  const g = runGroups(run);
  assert.deepEqual(g.slice(0, 3).map(x => x.state), ['done', 'failed', 'cli']);
  run.steps[2].page = true; run.steps[2].detail = 'the governor refused: level L2';
  assert.equal(runGroups(run)[2].state, 'skipped');
  assert.equal(runHeading(run, g), 'node-down · Failed');
});

test('expansions are listed under the YAML step that made them, and a ramp beside later steps is in progress', () => {
  const run = { name: 'ramp', running: true, started: 1, steps: [
    { n: 1, at: 0, target: 'bmc-farm', ids: ['bmc-farm', 'gpu001'], do: { nodes: ['gpu001'], temp: 60 }, status: 'ok', t: 0 },
    { n: 2, at: 5, target: 'slurm', ids: ['slurmctld', 'cn001'], do: { drain: ['cn001'] }, status: 'ok', t: 5 },
    { n: 1, at: 10, target: 'bmc-farm', ids: ['bmc-farm', 'gpu001'], do: { nodes: ['gpu001'], temp: 70 }, status: 'pending', t: null }] };
  const g = runGroups(run);
  assert.deepEqual(g.map(x => [x.n, x.rows.length, x.state, x.current]), [[1, 2, 'active', false], [2, 1, 'now', true]]);
  assert.equal(railRow(g[0]).extra, '2 actions');
});

test('one badge per part: the newest step that reached it, +N for the others, in step order for Tab', () => {
  const g = runGroups(nodeDown(['ok', 'ok', 'ok']));
  const b = partBadges(g), by = Object.fromEntries(b.map(x => [x.id, x]));
  assert.deepEqual(b.map(x => x.id), ['bmc-farm', 'bcm-mock', 'cn002', 'slurmctld']);  // sorted by step number, then id
  assert.deepEqual([by['cn002'].n, by['cn002'].glyph, by['cn002'].more], [3, '●', 5]);
  assert.deepEqual([by['bmc-farm'].n, by['bmc-farm'].glyph, by['bmc-farm'].more, by['bmc-farm'].others], [1, '✓', 1, [4]]);
  assert.match(by['bmc-farm'].label, /^Step 1 of 6, done: Power Off cn002\. Also step 4 on this part\.$/);
  assert.match(by['slurmctld'].label, /^Step 3 of 6, now: Drain cn002/);
  const before = partBadges(runGroups(nodeDown([])));  // before anything ran: the first step that will reach each part
  assert.ok(before.every(x => x.glyph === '○') && before.find(x => x.id === 'cn002').n === 1);
});

test('a part the diagram does not draw gets no badge, and the rail says why in words', () => {
  const g = runGroups(nodeDown(['ok']));
  const drawn = id => id !== 'cn002';
  assert.ok(!partBadges(g, drawn).some(x => x.id === 'cn002'));
  assert.equal(offDiagram(g[0], drawn), '');
  assert.equal(offDiagram(g[0], () => false), 'bmc-farm, cn002 are not in this cluster');
  const none = runGroups({ name: 'x', running: false, steps: [{ n: 1, at: 0, target: 'netem', ids: [], no_part: 'it clears the network delay on every node at once, not on one part', do: { clear: true }, status: 'skipped', detail: 'netem steps need the host CLI' }] });
  assert.equal(railRow(none[0]).off, 'Not on the diagram: it clears the network delay on every node at once, not on one part.');
  assert.equal(railRow(none[0]).word, 'CLI only');
});

test('rail rows carry the planned time, when it ran, and the parts', () => {
  const r = railRow(runGroups(nodeDown(['ok', 'ok', 'ok']))[2]);
  assert.deepEqual([r.n, r.glyph, r.word, r.at, r.ran, r.where, r.off], [3, '●', 'now', 'at 5 s', 'ran at 5 s', 'on slurmctld, cn002', '']);
  assert.equal(railRow(runGroups(nodeDown(['ok']))[0], () => true, id => ({ 'bmc-farm': 'BMC farm' })[id] || id).where, 'on BMC farm, cn002');
});

test('expectation pins: E numbers, CLI only before Play, the result once known', () => {
  const run = nodeDown(['ok']);
  assert.deepEqual(expectPins(run).map(p => [p.e, p.state, p.glyph, p.word]), [[1, 'cli', '↷', 'CLI only']]);
  run.plan_expects[0].page = true;
  assert.equal(expectPins(run)[0].word, 'waiting');
  run.expects = [{ label: run.plan_expects[0].label, status: 'ok', t: 12 }];
  assert.deepEqual([expectPins(run)[0].glyph, expectPins(run)[0].word, expectPins(run)[0].t], ['✓', 'held', 12]);
  run.expects[0].status = 'failed';
  assert.match(expectPins(run)[0].aria, /^Expectation E1, did not hold: slurm\.node_state/);
});

test('the phone sheet: the chosen step, else the current one, with Previous and Next', () => {
  const g = runGroups(nodeDown(['ok', 'ok', 'ok']));
  const s = sheetFor(g, focusStep(g, null));
  assert.deepEqual([s.n, s.glyph, s.title, s.prev, s.next, s.total], [3, '●', 'Step 3: Drain cn002 in Slurm (node down)', 2, 4, 6]);
  assert.equal(s.line, 'ran at 5 s · on slurmctld, cn002 · now');
  assert.equal(sheetFor(g, g[3]).line, 'at 40 s · on bmc-farm, cn002 · next');
  assert.equal(focusStep(g, 6).n, 6);
  assert.equal(sheetFor(g, g[0]).prev, null); assert.equal(sheetFor(g, g[5]).next, null);
  assert.equal(focusStep(g, 99).n, 3);  // a choice that no longer exists falls back to the current step
  assert.equal(sheetFor([], null), null);
});

test('the redraw key changes only when something drawn changes', () => {
  const a = nodeDown(['ok']), b = { ...nodeDown(['ok']), elapsed: 9 };
  assert.equal(runSig(a), runSig(b));
  assert.notEqual(runSig(a), runSig(nodeDown(['ok', 'ok'])));
  assert.equal(runSig(null), ''); assert.deepEqual(runGroups(null), []);
});
