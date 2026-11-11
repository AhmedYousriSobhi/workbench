import { test } from 'node:test';
import assert from 'node:assert/strict';
import { FILTERS, PER_PAGE, filterCounts, gpuBars, measured, pageOf, poolKpis, stateBar } from '../../static/js/poollayout.js';
import { fleetCard, fleetCards, health } from '../../static/js/fleetlayout.js';

const gpu = (i, o = {}) => ({ name: 'gn' + String(i).padStart(3, '0'), kind: 'gpu', gpus: 8, gpus_busy: 0, gpu_util: 0, state: 'ok', label: 'Healthy', ...o });
const cpu = i => ({ name: 'cn' + String(i).padStart(3, '0'), kind: 'compute', gpus: 0, state: 'ok', label: 'Healthy' });
const many = (n, f) => Array.from({ length: n }, (_, i) => f(i + 1));

test('a page holds 16 tiles, the page number is clamped and a filter shrinks the list', () => {
  const nodes = many(40, i => gpu(i, i % 4 === 0 ? { state: 'fault' } : {}));
  assert.equal(PER_PAGE, 16);
  assert.deepEqual([pageOf(nodes, 'all', 0).items.length, pageOf(nodes, 'all', 2).items.length, pageOf(nodes, 'all', 2).pages], [16, 8, 3]);
  assert.equal(pageOf(nodes, 'all', 99).page, 2);
  assert.equal(pageOf(nodes, 'all', -5).page, 0);
  assert.equal(pageOf(nodes, 'attention', 0).total, 10);
  assert.deepEqual(pageOf([], 'all', 3), { items: [], page: 0, pages: 1, total: 0 });
  assert.equal(pageOf(many(200, cpu), 'all', 0).pages, 13);
});

test('busy and idle exist only where something measures use; a CPU pool never claims either', () => {
  const cpus = many(6, cpu), gpus = many(4, i => gpu(i, { gpus_busy: i === 1 ? 3 : 0 }));
  assert.equal(measured(cpus), false); assert.equal(measured(gpus), true);
  const c = filterCounts(cpus), g = filterCounts(gpus);
  assert.deepEqual([c.busy.enabled, c.idle.enabled, c.all.enabled, c.attention.enabled], [false, false, true, true]);
  assert.deepEqual([g.busy.n, g.idle.n, g.all.n], [1, 3, 4]);
  assert.deepEqual(FILTERS.map(f => f[0]), ['all', 'busy', 'idle', 'attention']);
});

test('the KPIs count what is known and say null for what is not', () => {
  const k = poolKpis([gpu(1, { gpus_busy: 2, gpu_util: 40 }), gpu(2, { gpu_util: 20, state: 'warn' }), gpu(3, { state: 'fault' })]);
  assert.deepEqual([k.total, k.inService, k.attention, k.gpus, k.gpusBusy, k.avgUtil], [3, 1, 2, 24, 2, 20]);
  assert.equal(poolKpis(many(3, cpu)).avgUtil, null);
  assert.equal(poolKpis([]).avgUtil, null);
});

test('the state bar has one segment per state that occurs and sums to 100', () => {
  const bar = stateBar([gpu(1), gpu(2), gpu(3, { state: 'warn' }), gpu(4, { state: 'down' })]);
  assert.deepEqual(bar.map(b => [b.state, b.n]), [['ok', 2], ['warn', 1], ['down', 1]]);
  assert.equal(Math.round(bar.reduce((s, b) => s + b.pct, 0)), 100);
  assert.deepEqual(stateBar([]), []);
});

test('GPU bars light the busy ones, scale above 8 and say nothing for a node that reports none', () => {
  assert.deepEqual(gpuBars(gpu(1, { gpus_busy: 3 })).bars, [true, true, true, false, false, false, false, false]);
  assert.match(gpuBars(gpu(1, { gpus_busy: 3, gpu_util: 41 })).text, /3 of 8 GPUs busy · 41 % average/);
  assert.equal(gpuBars({ name: 'k', gpus: 8, kind: 'k8s' }), null);
  assert.equal(gpuBars(cpu(1)), null);
  assert.equal(gpuBars(gpu(1, { gpus: 16, gpus_busy: 16 })).bars.length, 8);
});

test('a card is healthy only when every node is, and its words give the count', () => {
  assert.deepEqual(health(many(3, cpu)), { state: 'ok', text: 'Healthy' });
  assert.deepEqual(health([cpu(1), cpu(2), { ...cpu(3), state: 'fault' }]), { state: 'fault', text: '1 of 3 needs attention' });
  assert.deepEqual(health([{ ...cpu(1), state: 'warn' }, { ...cpu(2), state: 'down' }]), { state: 'down', text: '2 of 2 need attention' });
  assert.equal(health([]).state, 'unknown');
});

test('a fleet card has the same structure for every cluster and marks what the cluster lacks', () => {
  const full = fleetCard({ name: 'a', nodes: [...many(3, cpu), { name: 'k1', kind: 'k8s', gpus: 0, state: 'ok' }], services: { controller: true }, logins: [{ name: 'login', kind: 'both' }] }, { known: true, dbd: true, rpm: 12 });
  const bare = fleetCard({ name: 'b', nodes: many(2, cpu), services: {}, logins: [{ name: 'login', kind: 'slurm' }] });
  assert.deepEqual(full.badges.map(b => b.k), bare.badges.map(b => b.k));
  assert.deepEqual(full.badges.map(b => b.on), [true, true, true, true]);
  assert.deepEqual(bare.badges.map(b => [b.on, !!b.dashed]), [[true, false], [false, false], [false, true], [false, true]]);
  assert.equal(bare.badges[2].label, 'no controller'); assert.equal(bare.badges[3].label, 'accounting not probed');
  assert.deepEqual([full.kpis.inService, full.kpis.total, full.kpis.rpm, bare.kpis.rpm], [4, 4, 12, null]);
  assert.deepEqual(bare.lanes.map(l => [l.k, l.present, l.count]), [['slurm', true, 2], ['k8s', false, 0]]);
});

test('one cluster gives one card, several give one each, and only the running one carries a rate', () => {
  const nodes = many(2, cpu), logins = [{ name: 'login', kind: 'both' }];
  assert.equal(fleetCards({ nodes, services: {}, logins }, { known: true, rpm: 5 }).length, 1);
  const two = fleetCards({ nodes, services: {}, logins, clusters: [{ name: 'c1', nodes, services: {}, logins }, { name: 'c2', nodes: [], services: {}, logins }] }, { known: true, rpm: 5 });
  assert.deepEqual(two.map(c => [c.name, c.kpis.rpm]), [['c1', null], ['c2', null]]);
  assert.equal(two[1].health.state, 'unknown');
});

test('a big pool draws one square per 8 nodes in a card lane', () => {
  const c = fleetCard({ name: 'big', nodes: many(200, cpu), services: {}, logins: [] });
  assert.deepEqual([c.lanes[0].per, c.lanes[0].squares.length], [8, 25]);
});
