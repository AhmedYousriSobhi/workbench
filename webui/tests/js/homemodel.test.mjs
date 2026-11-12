import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { attentionItems, homeNodeCard, homeServices, homeSummary } from '../../static/js/homemodel.js';

const LV = ['Normal', 'Watching', 'Throttling', 'Freezing', 'Stopping', 'Emergency'];
const cl = (nodes, services) => ({ nodes, services });
const n = (name, state, extra = {}) => ({ name, kind: 'compute', state, label: state === 'ok' ? 'idle' : 'drained', gpus: 0, ...extra });

test('no governor is one item, a clear cluster has none', () => {
  assert.deepEqual(attentionItems(null, null, LV).map(i => i.kind), ['bad']);
  assert.deepEqual(attentionItems({ level: 0 }, cl([n('cn001', 'ok')], { slurm: true }), LV), []);
});
test('pressure from L2, findings are capped at five', () => {
  const g = { level: 3, violations: [1, 2, 3, 4, 5, 6, 7] };
  const items = attentionItems(g, null, LV);
  assert.match(items[0].text, /L3: freezing/); assert.equal(items.length, 6);
});
test('a stack that is down is one message, not one per node', () => {
  const items = attentionItems({ level: 0 }, cl([n('a', 'unknown'), n('b', 'unknown')], { x: false, y: false }), LV);
  assert.equal(items.length, 1); assert.match(items[0].text, /not running/);
});
test('a service down and a drained node name themselves; the node item carries its name', () => {
  const items = attentionItems({ level: 0 }, cl([n('gpu001', 'warn'), n('cn001', 'ok')], { jira: false, slurm: true }), LV);
  assert.equal(items.length, 2); assert.equal(items[1].node, 'gpu001'); assert.equal(items[1].kind, 'warn');
});
test('a node card shows a load bar only when the exporter measured it', () => {
  assert.equal(homeNodeCard(n('cn001', 'ok')).load, null);
  const g = homeNodeCard({ name: 'gpu001', kind: 'gpu', state: 'warn', label: 'drained', gpus: 8, gpus_busy: 5, gpu_util: 62.4, temp: 61.2 });
  assert.equal(g.load, 62); assert.equal(g.kind, 'GPU · 8 GPUs'); assert.match(g.detail, /5 of 8 GPUs busy · 61 °C/); assert.equal(g.icon, '▲');
  assert.equal(homeNodeCard(n('cn001', 'ok', { power: 'On' })).detail, 'On');
  assert.equal(homeNodeCard(n('cn001', 'ok')).detail, 'no live reading');
});
test('a node card clamps nonsense and names Kubernetes nodes', () => {
  assert.equal(homeNodeCard({ name: 'g', kind: 'gpu', state: 'ok', gpus: 1, gpu_util: 180 }).load, 100);
  assert.equal(homeNodeCard({ name: 'k', kind: 'k8s', state: 'ok', gpus: 0 }).kind, 'Kubernetes');
});
test('services are words and a real/simulated tag', () => {
  const s = homeServices({ prometheus: true, 'jira-real': false }, new Set(['jira-real']));
  assert.deepEqual(s.map(x => [x.icon, x.text, x.real]), [['✓', 'answering', false], ['✗', 'not answering', true]]);
});
test('the summary line', () => {
  assert.match(homeSummary(null), /Waiting/);
  assert.match(homeSummary(cl([n('a', 'ok'), n('b', 'warn')], { x: true })), /1 of 2 nodes healthy · 1 of 1 services answering/);
  assert.match(homeSummary(cl([], { x: false })), /not running/);
});
