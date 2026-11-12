import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { applyOutcome, stageRows } from '../../static/js/applymodel.js';

const change = (state, stages, extra = {}) => ({ job: 4, label: 'apply-plan', state, stages, started: 100, finished: null, ...extra });

test('stage rows say waiting, running, seconds and failed in words', () => {
  const rows = stageRows(change('running', [{ name: 'Build images', state: 'done', secs: 12.4 }, { name: 'Nodes', state: 'running', secs: null }, { name: 'Login node', state: 'waiting', secs: null }]));
  assert.deepEqual(rows.map(r => [r.icon, r.t]), [['✓', '12 s'], ['▶', 'running'], ['○', 'waiting']]);
  assert.equal(stageRows(change('failed', [{ name: 'Nodes', state: 'failed', secs: null }]))[0].t, 'failed');
  assert.deepEqual(stageRows(null), []);
  assert.equal(stageRows(change('done', [{ name: 'X', state: 'done', secs: null }]))[0].t, 'done');  // no time is invented
});

test('a change of another job, or none, is reported as unknown, not guessed', () => {
  assert.equal(applyOutcome(null, 4).kind, 'unknown');
  assert.equal(applyOutcome(change('done', []), 5).kind, 'unknown');
});

test('a running job names the current stage and counts the finished ones', () => {
  const o = applyOutcome(change('running', [{ name: 'Build images', state: 'done', secs: 3 }, { name: 'Nodes', state: 'running', secs: null }]), 4);
  assert.equal(o.kind, 'running');
  assert.match(o.text, /nodes \(1 of 2 stages done\)/);
  assert.match(applyOutcome(change('running', []), 4).text, /starting/);
});

test('done and failed jobs say how long they took and where they stopped', () => {
  assert.equal(applyOutcome(change('done', [], { finished: 142 }), 4).text, 'Applied (job #4, 42 s).');
  const f = applyOutcome(change('failed', [{ name: 'Slurm core', state: 'failed', secs: null }], { finished: 130 }), 4);
  assert.equal(f.kind, 'failed');
  assert.match(f.text, /Failed at slurm core \(job #4, 30 s\)\. The reason is in Governor activity/);
});
