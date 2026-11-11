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
test('a Kubernetes node has no BMC or address rows', () => {
  const rows = nodeSummary({ name: 'k8s-gpu001', kind: 'k8s', gpus: 8, state: 'ok' }, null);
  assert.equal(get(rows, 'BMC'), undefined); assert.match(get(rows, 'Kind').v, /KWOK\), 8 GPUs/);
  assert.deepEqual(nodeSummary(null, null), []);
});
test('lens chips: Telemetry is shown as Monitoring, ids are the plane ids, All is first', () => {
  assert.equal(LENSES[0].id, 'all');
  assert.equal(LENSES.find(l => l.id === 'telemetry').label, 'Monitoring');
  assert.deepEqual(LENSES.map(l => l.id), ['all', 'access', 'management', 'telemetry', 'tickets', 'faults', 'client']);
});
