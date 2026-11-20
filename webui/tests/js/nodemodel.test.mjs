import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { LENSES, nodeSummary } from '../../static/js/nodemodel.js';

const gpu = (extra = {}) => ({ name: 'gpu001', kind: 'gpu', state: 'warn', label: 'drained', gpus: 8, temp: 88.4, fans_failed: 0, xid: 79, power: 'On', ...extra });
const get = (rows, k) => rows.find(r => r.k === k);

test('a GPU node says what the page measured, and names what it did not', () => {
  const rows = nodeSummary(gpu(), { ip: '10.141.100.1', bmc_ip: '10.148.100.1' });
  assert.equal(get(rows, 'Hardware').v, '8 GPUs · 88 °C · fans ok');
  assert.equal(get(rows, 'Faults').v, 'GPU XID 79');
  assert.equal(get(rows, 'BMC').v, '10.148.100.1:443'); assert.ok(get(rows, 'BMC').code);
  assert.match(get(rows, 'Reason').v, /not read here.*describe node gpu001/);
});
test('a healthy node has no reason and no Faults row', () => {
  const rows = nodeSummary(gpu({ state: 'ok', xid: 0, label: 'idle' }), { ip: '10.141.100.1', bmc_ip: null });
  assert.equal(get(rows, 'Reason').v, 'none (healthy)'); assert.equal(get(rows, 'Faults'), undefined);
  assert.equal(get(rows, 'BMC').v, 'none for this node');
});
test('no reading is "not measured", never zero or blank; failed fans are counted', () => {
  const rows = nodeSummary(gpu({ temp: null, fans_failed: 2, power: null, xid: 0 }), null);
  assert.equal(get(rows, 'Hardware').v, '8 GPUs · 2 fans failed');
  assert.equal(get(rows, 'Power').v, 'not measured'); assert.equal(get(rows, 'Address').v, 'not measured'); assert.equal(get(rows, 'BMC').v, 'not measured');
});
test('a Kubernetes node shows its machine (power, temperature, BCM) but no BMC or address rows', () => {
  const rows = nodeSummary({ name: 'k8s-gpu001', kind: 'k8s', gpus: 8, state: 'fault', label: 'NotReady (Powered off)', power: 'Off', temp: 18, fans_failed: 0, bcm: 'DOWN' }, null);
  assert.equal(get(rows, 'BMC'), undefined); assert.match(get(rows, 'Kind').v, /KWOK\), 8 GPUs/);
  assert.equal(get(rows, 'Power').v, 'Off'); assert.equal(get(rows, 'BCM').v, 'DOWN'); assert.equal(get(rows, 'Hardware').v, '18 °C · fans ok');
  assert.match(get(rows, 'Reason').v, /^NotReady \(Powered off\); /);
  assert.equal(get(nodeSummary({ name: 'k', kind: 'k8s', state: 'unknown' }, null), 'Power').v, 'not measured');
  assert.deepEqual(nodeSummary(null, null), []);
});
test('lens chips: Telemetry is shown as Monitoring, ids are the plane ids, All is first', () => {
  assert.equal(LENSES[0].id, 'all');
  assert.equal(LENSES.find(l => l.id === 'telemetry').label, 'Monitoring');
  assert.deepEqual(LENSES.map(l => l.id), ['all', 'access', 'management', 'telemetry', 'tickets', 'faults', 'client']);
});
test('Slurm reason and running jobs are shown when the stats are readable', () => {
  const rows = nodeSummary(gpu(), null, { reason: 'Kill task failed', jobs: [{ id: '12', user: 'alice' }, { id: '13_4', user: 'bob' }] });
  assert.equal(get(rows, 'Reason').v, "Kill task failed (Slurm's reason)");
  assert.equal(get(rows, 'Jobs').v, '12 (alice), 13_4 (bob)');
  const idle = nodeSummary(gpu({ state: 'ok', xid: 0 }), null, { reason: undefined, jobs: [] });
  assert.equal(get(idle, 'Reason').v, 'none (healthy)'); assert.equal(get(idle, 'Jobs').v, 'none running');
});
test('without Slurm stats there is one notice and no Jobs row', () => {
  const rows = nodeSummary(gpu(), null, null);
  assert.equal(get(rows, 'Jobs'), undefined);
  assert.match(get(rows, 'Reason').v, /Slurm stats are unavailable.*describe node gpu001/);
});
test('the Tickets row: none, some, unreadable, and no row before it was asked', () => {
  assert.equal(get(nodeSummary(gpu(), null, null), 'Tickets'), undefined);
  assert.equal(get(nodeSummary(gpu(), null, null, []), 'Tickets').v, 'none in the Jira mock (label node-gpu001)');
  const t = [{ key: 'OPS-3', summary: 'gpu001: GPU fault', status: 'Open', priority: 'High' }];
  assert.equal(get(nodeSummary(gpu(), null, null, t), 'Tickets').v, 'OPS-3 Open, High: gpu001: GPU fault');
  assert.match(get(nodeSummary(gpu(), null, null, null), 'Tickets').v, /^not read/);
  assert.ok(get(nodeSummary({ name: 'k8s-gpu001', kind: 'k8s', state: 'ok' }, null, null, []), 'Tickets'));
});
