// The Cluster level's layout and wiring decisions (redesign step 3): what is placed where, what is greyed, how racks summarise.
import './setup.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { circuitSpec, circuitFacts, rackSquares, layerParts, LAYER_OF, LAYERS, SQUARE_MAX, PER_GROUP, CV_W, CV_H } from '../../static/js/circuitlayout.js';
import { wireLayers, wireLabel } from '../../static/js/wire.js';

const node = (name, kind, state = 'ok', gpus = 0) => ({ name, kind, state, label: state === 'ok' ? 'Healthy' : 'drained', gpus });
const slurmOnly = { nodes: [node('cn001', 'compute'), node('gpu001', 'gpu', 'ok', 8)], logins: [{ name: 'login', mode: 'ssh', kind: 'slurm' }], services: { 'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'gpu-exporter': true } };
const full = { nodes: [...slurmOnly.nodes, node('kw001', 'k8s'), node('kw002', 'k8s', 'ok', 8)], logins: [{ name: 'login', mode: 'both', kind: 'both' }], services: { ...slurmOnly.services, 'k8s-api': true, controller: true, jump: true, teleport: true } };
const k8sOnly = { nodes: [node('kw001', 'k8s')], logins: [{ name: 'k-login', mode: 'ssh', kind: 'k8s' }], services: { 'k8s-api': true, 'bmc-farm': true } };

test('every cluster gets the same parts and the same wires in the same places (W-17)', () => {
  const specs = [slurmOnly, full, k8sOnly, { nodes: [], logins: [], services: {} }].map(c => circuitSpec(circuitFacts(c)));
  const ids = s => Object.keys(s.parts).filter(k => !s.row.includes(k) || ['slurmrestd', 'bmc-farm', 'bcm-mock', 'gpu-exporter', 'prometheus', 'grafana', 'jira-mock'].includes(k));
  for (const s of specs.slice(1)) {
    assert.deepEqual(ids(s), ids(specs[0]));
    for (const id of ids(s)) assert.deepEqual([s.parts[id].x, s.parts[id].y, s.parts[id].w, s.parts[id].h], [specs[0].parts[id].x, specs[0].parts[id].y, specs[0].parts[id].w, specs[0].parts[id].h], id);
    assert.deepEqual(s.wires.map(w => w.key), specs[0].wires.map(w => w.key));
    assert.deepEqual(s.wires.map(w => w.d), specs[0].wires.map(w => w.d));
  }
});

test('a part a cluster lacks is greyed, not dropped', () => {
  const s = circuitSpec(circuitFacts(slurmOnly));
  assert.equal(s.parts['k8s-api'].present, false); assert.equal(s.parts['rack-kg'].present, false); assert.equal(s.bands.k8s.present, false);
  assert.equal(s.parts['rack-sg'].present, true); assert.equal(s.bands.slurm.present, true);
  const k = circuitSpec(circuitFacts(k8sOnly));
  assert.equal(k.bands.slurm.present, false); assert.equal(k.parts.slurmctld.present, false); assert.equal(k.parts.klogin.present, true);
  assert.ok(k.bands.slurm.note.includes('no Slurm') && s.bands.k8s.note.includes('no Kubernetes'));
});

test('controller variants: with a controller the admin SSH wire is drawn and login has no head-node badge; without, the reverse', () => {
  const w = (s, key) => s.wires.find(x => x.key === key);
  const withCtl = circuitSpec(circuitFacts(full)), without = circuitSpec(circuitFacts(slurmOnly));
  assert.equal(withCtl.parts.slurmctld.ctl, true); assert.equal(withCtl.parts.login.head, false); assert.equal(w(withCtl, 'clients-controller').present, true);
  assert.equal(without.parts.slurmctld.ctl, false); assert.equal(without.parts.login.head, true); assert.equal(w(without, 'clients-controller').present, false);
  assert.equal(w(without, 'jump-controller').present, false);
});

test('a login serving both lanes also feeds the kubectl slot, saying which login it is', () => {
  const s = circuitSpec(circuitFacts(full));
  assert.equal(s.parts.klogin.present, true); assert.equal(s.parts.klogin.via, true);
  assert.equal(circuitSpec(circuitFacts(k8sOnly)).parts.klogin.via, false);
});

test('racks: one square per node up to 64, one per 8 above, coloured by the worst node', () => {
  const mk = n => Array.from({ length: n }, (_, i) => node('n' + i, 'compute'));
  assert.equal(rackSquares(mk(SQUARE_MAX)).squares.length, SQUARE_MAX); assert.equal(rackSquares(mk(SQUARE_MAX)).per, 1);
  const big = mk(SQUARE_MAX + 1); big[9].state = 'warn'; big[10].state = 'fault';
  const r = rackSquares(big);
  assert.equal(r.per, PER_GROUP); assert.equal(r.squares.length, Math.ceil(65 / 8));
  assert.equal(r.squares[1].state, 'fault'); assert.equal(r.squares[0].state, 'ok'); assert.match(r.squares[1].label, /2 of 8 need attention/);
  assert.equal(r.squares.at(-1).names.length, 1);
});

test('wires stay inside the drawing and are straight segments', () => {
  const s = circuitSpec(circuitFacts(full));
  for (const w of s.wires) { const n = w.d.match(/-?\d+/g).map(Number); for (let i = 0; i < n.length; i += 2) { assert.ok(n[i] >= 0 && n[i] <= CV_W && n[i + 1] >= 0 && n[i + 1] <= CV_H, `${w.key} ${w.d}`); } assert.match(w.d, /^M[\d -]+( L[\d -]+)+$/, w.key); }
  assert.equal(new Set(s.wires.map(w => w.key)).size, s.wires.length);
});

test('every wire belongs to a layer of the stack; isolating a layer lights only the parts its wires touch', () => {
  const s = circuitSpec(circuitFacts(full)), names = LAYERS.map(l => l[0]);
  for (const w of s.wires) assert.ok(names.includes(LAYER_OF[w.plane]), `${w.key}: ${w.plane}`);
  assert.equal(layerParts(s, 'all'), null);
  const mon = layerParts(s, 'monitoring');
  assert.ok(mon.has('gpu-exporter') && mon.has('prometheus') && !mon.has('login') && !mon.has('rack-sg'));
  assert.equal(LAYER_OF.k8s, 'control');
});

test('services row: the fixed services, then the opt-in real parts the cluster has', () => {
  const plain = circuitSpec(circuitFacts(slurmOnly)).row, withReal = circuitSpec(circuitFacts({ ...slurmOnly, services: { ...slurmOnly.services, 'dcgm-exporter': true } })).row;
  assert.deepEqual(plain, ['slurmrestd', 'bmc-farm', 'bcm-mock', 'gpu-exporter', 'prometheus', 'grafana', 'jira-mock']);
  assert.deepEqual(withReal.slice(-1), ['dcgm-exporter']);
});

test('a declared wire is lit but still: nothing counts it, so nothing moves and the words say so', () => {
  const d = 'M0 0 L10 0';
  assert.deepEqual(wireLayers({ d, declared: true }).map(l => l.cls), ['wire-base', 'wire-halo', 'wire-core']);
  assert.deepEqual(wireLayers({ d, declared: false }).map(l => l.cls), ['wire-base', 'wire-idle']);
  assert.deepEqual(wireLayers({ d, declared: true, rpm: 30 }).map(l => l.cls).slice(-1), ['wire-pulse']);
  assert.match(wireLabel({ plane: 'access', declared: true }), /declared wiring, not measured/);
  assert.match(wireLabel({ plane: 'access' }), /no current/);
});
