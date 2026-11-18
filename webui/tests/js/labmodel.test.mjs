import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { stepRows, verdictView, resultBars, secs, diffTag, validationRows, refusal } from '../../static/js/labmodel.js';

test('the stepper has six steps and defaults to todo', () => {
  const rows = stepRows({ import: 'done', apply: 'now' });
  assert.deepEqual(rows.map(r => r.state), ['done', 'todo', 'todo', 'now', 'todo', 'todo']);
  assert.equal(rows[4].label, 'A/B replay');
});
test('verdicts carry a state and a word; none is a warning', () => {
  assert.deepEqual(verdictView('Do not ship'), { text: 'Do not ship', state: 'bad' });
  assert.equal(verdictView('Ship').state, 'ok');
  assert.equal(verdictView(null).state, 'warn');
});
test('bars use production-equivalent time and scale to the larger side', () => {
  const rep = { factor: 10, a: { by_partition: { gpu: { wait_median_s: 6 } } }, b: { by_partition: { gpu: { wait_median_s: 3 }, cpu: { wait_median_s: null } } } };
  const bars = resultBars(rep);
  assert.deepEqual(bars.map(b => b.partition), ['cpu', 'gpu']);
  assert.equal(bars[1].a, 60); assert.equal(bars[1].aPct, 100); assert.equal(bars[1].bPct, 50); assert.equal(bars[0].b, null);
  assert.deepEqual(resultBars(null), []);
});
test('seconds read as seconds, minutes or hours', () => {
  assert.equal(secs(null), '–'); assert.equal(secs(30), '30 s'); assert.equal(secs(600), '10 min'); assert.equal(secs(7200), '2.0 h');
});
test('diff tags, validation rows and refusals', () => {
  assert.equal(diffTag({ note: 'needs a slurmctld restart' }), 'restart'); assert.equal(diffTag({ note: '' }), 'live');
  assert.deepEqual(validationRows({ results: [{ tool: 'promtool', status: 'passed', messages: ['ok', 'x'] }] }), [{ tool: 'promtool', status: 'passed', first: 'ok' }]);
  assert.match(refusal(503), /governor is not reachable/); assert.equal(refusal(409, 'L2'), 'L2');
});
