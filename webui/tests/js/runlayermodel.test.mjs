// The run layer's logic (ADR 0086): steps grouped by their YAML number, one badge per part, state glyphs and words, Tab order,
// expectation pins and the phone sheet's words. runlayer.js draws what these return.
import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { PART_WORDS, RUN_GLYPH, RUN_WORD, blastLine, cliNote, fromCli, sourceWords, partWords, replayCaption, replayClamp, replayGroups, replayHeading, replayKey, replayPins, replayStops, replayText, replayable, blastMarks, expectPins, focusStep, ghostParts, offDiagram, partBadges, previewRun, previewSummary, railRow, runGroups, runHeading, runKey, runSig, sheetFor } from '../../static/js/runlayermodel.js';

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
  assert.equal(offDiagram(g[0], () => false), 'the BMC farm, cn002 are not in this cluster');
  const none = runGroups({ name: 'x', running: false, steps: [{ n: 1, at: 0, target: 'netem', ids: [], no_part: 'it clears the network delay on every node at once, not on one part', do: { clear: true }, status: 'skipped', detail: 'netem steps need the host CLI' }] });
  assert.equal(railRow(none[0]).off, 'Not on the diagram: it clears the network delay on every node at once, not on one part.');
  assert.equal(railRow(none[0]).word, 'CLI only');
});

test('rail rows carry the planned time, when it ran, and the parts', () => {
  const r = railRow(runGroups(nodeDown(['ok', 'ok', 'ok']))[2]);
  assert.deepEqual([r.n, r.glyph, r.word, r.at, r.ran, r.where, r.off], [3, '●', 'now', 'at 5 s', 'ran at 5 s', 'on slurmctld (the Slurm controller), cn002', '']);
  assert.equal(railRow(runGroups(nodeDown(['ok']))[0], () => true, id => ({ cn002: 'node cn002' })[id] || id).where, 'on the BMC farm, node cn002');
});

test('expectation pins: E numbers, CLI only before Play, the result once known', () => {
  const run = nodeDown(['ok']);
  assert.deepEqual(expectPins(run).map(p => [p.e, p.state, p.glyph, p.word]), [[1, 'cli', '↷', 'CLI only']]);
  run.plan_expects[0].page = true;
  assert.equal(expectPins(run)[0].word, 'waiting');
  run.expects = [{ label: run.plan_expects[0].label, status: 'ok', t: 12 }];
  assert.deepEqual([expectPins(run)[0].glyph, expectPins(run)[0].word, expectPins(run)[0].t], ['✓', 'held', 12]);
  run.expects[0].status = 'failed';
  assert.equal(expectPins(run)[0].aria, 'Expectation E1, did not hold: Slurm shows cn002 as drained');
});

test('the phone sheet: the chosen step, else the current one, with Previous and Next', () => {
  const g = runGroups(nodeDown(['ok', 'ok', 'ok']));
  const s = sheetFor(g, focusStep(g, null));
  assert.deepEqual([s.n, s.glyph, s.title, s.prev, s.next, s.total], [3, '●', 'Step 3: Drain cn002 in Slurm (node down)', 2, 4, 6]);
  assert.equal(s.line, 'ran at 5 s · on slurmctld (the Slurm controller), cn002 · now');
  assert.equal(sheetFor(g, g[3]).line, 'at 40 s · on the BMC farm, cn002 · next');
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

// ---- Phase 2: before Play (ADR 0086, issue 809) ----
// node-down's preview as GET /api/scenarios/node-down answers it: the run state's steps, nothing applied, plus each step's declared blast
const preview = () => {
  const r = nodeDown([]);
  return { name: 'node-down', ref: 'SC-17', steps: r.steps.map(({ status, t, detail, ...x }) => ({ ...x, blast: x.target === 'slurm' ? ['slurmrestd', 'login'] : [x.target],
    blast_caption: `${x.target} changes.` })), expects: r.plan_expects };
};

test('a preview is a run that has not started: every step planned, a CLI-only step marked, nothing reached', () => {
  const p = preview(); p.steps[2].page = false;
  const run = previewRun(p), g = runGroups(run);
  assert.equal(run.preview, true); assert.equal(runKey(run), 'preview:node-down'); assert.equal(runKey({ name: 'x', started: 5 }), 'x@5');
  assert.deepEqual(g.map(x => x.state), ['planned', 'planned', 'cli', 'planned', 'planned', 'planned']);
  assert.ok(g.every(x => !x.current && x.t == null));
  assert.equal(runHeading(run, g), 'node-down · Preview: nothing has run yet');
  assert.equal(focusStep(g, null).n, 1, 'nothing is reached before Play, not even a CLI-only step');
  assert.equal(previewRun(null), null); assert.equal(previewRun({ name: 'x' }), null);
});

test('ghost badges: one per part, the first step that will act there, the word "planned" in every label', () => {
  const g = runGroups(previewRun(preview())), b = partBadges(g), by = Object.fromEntries(b.map(x => [x.id, x]));
  assert.ok(b.every(x => x.ghost && x.glyph === '○' && /, planned: /.test(x.label)));
  assert.equal(by['bmc-farm'].n, 1); assert.equal(by['bmc-farm'].more, 1);
  assert.match(by['bmc-farm'].label, /^Step 1 of 6, planned: Power Off cn002\. Also step 4 on this part\.$/);
  assert.deepEqual(b.map(x => x.n), [...b.map(x => x.n)].sort((x, y) => x - y), 'Tab follows the scenario');
});

test('Play turns ghost badges into live ones on the same parts, in the same order (no jump)', () => {
  const ghost = partBadges(runGroups(previewRun(preview()))), live = partBadges(runGroups(nodeDown(['ok'])));
  assert.deepEqual(live.map(x => x.id), ghost.map(x => x.id));
  assert.ok(live.every(x => !x.ghost));
  assert.notEqual(runSig(previewRun(preview())), runSig(nodeDown([])), 'the layer redraws when the run takes over');
});

test('before Play the expectation pin says planned, or CLI only with ↷', () => {
  const p = preview(), cli = expectPins(previewRun(p));
  assert.deepEqual([cli[0].state, cli[0].glyph, cli[0].word], ['cli', '↷', 'CLI only']);
  p.expects[0].page = true;
  const pin = expectPins(previewRun(p))[0];
  assert.deepEqual([pin.state, pin.glyph, pin.word], ['planned', '○', 'planned']);
  assert.equal(pin.aria, 'Expectation E1, planned: Slurm shows cn002 as drained');
});

test('the declared blast: whose clients may see a change, per step in words and once per part on the diagram', () => {
  const g = runGroups(previewRun(preview()));
  assert.equal(blastLine({ blast: ['cn002'], blastWhy: 'x.' }, id => id.toUpperCase()), 'Clients may see a change on CN002: x.');
  assert.equal(blastLine(g[0]), 'Clients may see a change on the BMC farm: bmc-farm changes.');
  assert.equal(railRow(g[2]).blast, 'Clients may see a change on slurmrestd (the Slurm REST API), the login node: slurm changes.');
  const marks = blastMarks(g, id => id !== 'login');
  assert.deepEqual(marks.map(m => [m.id, m.steps]), [['bmc-farm', [1, 4]], ['bcm-mock', [2, 5]], ['slurmrestd', [3, 6]]]);
  assert.match(marks[0].label, /steps 1, 4 \(declared, not measured\)/);
  assert.equal(blastLine({ blast: [] }), '');
});

test('the preview line: steps, parts, expectations and what only the CLI can run', () => {
  const p = preview(); p.steps[2].page = false;
  const run = previewRun(p), g = runGroups(run), pins = expectPins(run);
  assert.deepEqual(ghostParts(g), ['bmc-farm', 'cn002', 'bcm-mock', 'slurmctld']);
  assert.equal(previewSummary(g, pins), '6 steps touch 4 parts · 1 expectation · 2 items need the host CLI (marked ↷)');
  p.steps[2].page = true; p.expects[0].page = true;
  assert.equal(previewSummary(runGroups(previewRun(p)), expectPins(previewRun(p)), id => id !== 'cn002'), '6 steps touch 3 parts · 1 expectation · the page can run every item');
});

test('the phone sheet reads a preview step as planned', () => {
  const g = runGroups(previewRun(preview()));
  assert.equal(sheetFor(g, g[0]).line, 'at 0 s · on the BMC farm, cn002 · planned');
});

// ---- phase 3: replay over the recorded steps, and part ids in words ----
const ended = (statuses, extra = {}) => ({ ...nodeDown(statuses, false), ...extra });

test('replay walks the recorded steps only, in the order they ran', () => {
  assert.equal(replayable(nodeDown(['ok', 'ok'])), false, 'not while it runs');
  assert.equal(replayable(previewRun(preview())), false, 'not a preview');
  assert.equal(replayable(ended([])), false, 'nothing recorded');
  const stopped = ended(['ok', 'ok', 'ok'], { stopped: true }), g = runGroups(stopped), stops = replayStops(g);
  assert.ok(replayable(stopped));
  assert.deepEqual(stops.map(s => s.n), [1, 2, 3], 'steps 4 to 6 never ran: not stops');
  assert.equal(replayCaption(g, stops), 'Replay walks the 3 recorded steps only (3 never ran). Node states on the diagram stay live.');
  const late = ended(['ok', 'ok']); late.steps[0].t = 9;  // step 1 ran after step 2: the order is the time order
  assert.deepEqual(replayStops(runGroups(late)).map(s => s.n), [2, 1]);
});

test('at a stop: earlier steps keep their result, this one is now, later ones are next with no time', () => {
  const run = ended(['ok', 'ok', 'failed', 'ok', 'ok', 'ok']), g = runGroups(run), stops = replayStops(g);
  const at1 = replayGroups(g, stops, 1);
  assert.deepEqual(at1.map(x => x.state), ['done', 'now', 'next', 'next', 'next', 'next']);
  assert.deepEqual(at1.map(x => x.current), [false, true, false, false, false, false]);
  assert.deepEqual(at1.map(x => x.t), [0, 0, null, null, null, null]);
  assert.equal(replayGroups(g, stops, 2)[2].state, 'failed', 'a failure stays a failure, also as the current step');
  assert.deepEqual(replayGroups(g, stops, 5).map(x => x.state), ['done', 'done', 'failed', 'done', 'done', 'now']);
  assert.deepEqual(g.map(x => x.state), ['done', 'done', 'failed', 'done', 'done', 'done'], 'the recorded groups are not changed');
  const badges = partBadges(at1);
  assert.equal(badges.find(b => b.id === 'bcm-mock').state, 'now');
  assert.equal(badges.find(b => b.id === 'slurmctld').state, 'next');
});

test('the slider: keys of the WAI-ARIA pattern, clamped; aria-valuetext names the step and its result', () => {
  assert.deepEqual(['ArrowRight', 'ArrowUp', 'ArrowLeft', 'ArrowDown', 'Home', 'End'].map(k => replayKey(k, 2, 6)), [3, 3, 1, 1, 0, 5]);
  assert.equal(replayKey('ArrowRight', 5, 6), 5); assert.equal(replayKey('ArrowLeft', 0, 6), 0); assert.equal(replayKey('Enter', 2, 6), null);
  assert.equal(replayClamp(9, [1, 2, 3]), 2); assert.equal(replayClamp(-1, [1, 2, 3]), 0);
  const run = ended(['ok', 'ok', 'ok', 'ok', 'ok', 'ok']), g = runGroups(run), stops = replayStops(g);
  assert.equal(replayText(g, stops, 2), 'Step 3, 3 of 6 recorded: Drain cn002 in Slurm (node down), done, ran at 5 s');
  assert.equal(replayHeading(run, g, stops, 2), 'node-down · Replay: step 3, 3 of 6 recorded');
  assert.equal(replayText(g, [], 0), '');
});

test('in replay a pin shows its result only once it was known', () => {
  const run = ended(['ok', 'ok', 'ok', 'ok', 'ok', 'ok']); run.plan_expects[0].page = true;
  run.expects = [{ label: run.plan_expects[0].label, status: 'ok', t: 20 }];
  const g = runGroups(run), stops = replayStops(g), pins = expectPins(run);
  assert.deepEqual([replayPins(pins, stops, 2)[0].word, replayPins(pins, stops, 2)[0].glyph], ['waiting', '○'], 'step 3 ran at 5 s: not known yet');
  assert.equal(replayPins(pins, stops, 5)[0].word, 'held', 'the last stop shows every result');
});

test('every part id the engine names reads as words, a node keeps its name', () => {
  for (const id of ['login', 'slurmctld', 'slurmdbd', 'slurmrestd', 'teleport', 'k8s-api', 'jira-mock']) assert.notEqual(partWords(id), id, id);
  assert.equal(partWords('cn002'), 'cn002'); assert.equal(partWords('cn002', id => `node ${id}`), 'node cn002');
  assert.ok(Object.values(PART_WORDS).every(w => !/[{}"]/.test(w)));
});

// Phase 4: a run from the CLI. Its steps the page cannot run (page: false) ran on the host: they show their real result, never "CLI only".
const fromHost = (statuses, extra = {}) => {
  const r = nodeDown(statuses, extra.running ?? false);
  r.steps = r.steps.map(s => s.n === 3 || s.n === 6 ? { ...s, page: false } : s);
  return { ...r, source: 'cli', ok: true, ...extra };
};

test('a run from the CLI is said in words, and its CLI-only steps show their real result', () => {
  const run = fromHost(['ok', 'ok', 'ok', 'ok', 'ok', 'skipped']), g = runGroups(run);
  assert.ok(fromCli(run) && !fromCli(nodeDown([])) && sourceWords(run) === 'run from the CLI' && sourceWords(nodeDown([])) === '');
  assert.deepEqual(g.map(x => x.state), ['done', 'done', 'done', 'done', 'done', 'skipped']);  // a page run would say 'cli' for step 6
  assert.ok(partBadges(g).every(b => b.glyph !== '↷' || b.state === 'skipped') && !partBadges(g).some(b => b.label.includes('CLI only')));
  assert.equal(runHeading(run, g), 'node-down · run from the CLI · Passed, with skipped steps');
  assert.equal(runGroups({ ...run, source: undefined })[5].state, 'cli');
});

test('a CLI run\'s expectations show their result; one not checked yet waits instead of saying CLI only', () => {
  const live = fromHost(['ok'], { running: true, ok: null });
  assert.deepEqual(expectPins(live).map(p => [p.state, p.word]), [['next', 'waiting']]);
  const held = { ...fromHost(['ok', 'ok', 'ok', 'ok', 'ok', 'ok']), expects: [{ label: live.plan_expects[0].label, status: 'ok', t: 6 }] };
  assert.deepEqual(expectPins(held).map(p => [p.state, p.glyph, p.word]), [['done', '✓', 'held']]);
  assert.match(cliNote(live), /stop it with Ctrl\+C in its terminal/);
  assert.equal(cliNote(nodeDown([])), '');
});

test('a CLI run that went quiet says so, and its replay is marked too', () => {
  const lost = fromHost(['ok', 'ok'], { stopped: true, lost: true }), g = runGroups(lost), stops = replayStops(g);
  assert.equal(runHeading(lost, g), 'node-down · run from the CLI · No word from the CLI after step 2 of 6');
  assert.match(cliNote(lost), /sent nothing for 30 s/);
  assert.equal(replayHeading(lost, g, stops, 0), 'node-down · run from the CLI · Replay: step 1, 1 of 2 recorded');
  assert.notEqual(runSig(lost), runSig({ ...lost, source: 'page' }));
});
