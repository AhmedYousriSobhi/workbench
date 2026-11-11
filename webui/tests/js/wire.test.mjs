// The wire component's decisions: which strokes, how fast, what it says in words. No DOM needed.
import './setup.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pulseSeconds, errSeconds, wireLayers, wireLabel, PLANES, SLOWEST, FASTEST, PULSE_PERIOD, ERR_PERIOD } from '../../static/js/wire.js';

test('no requests means no current: nothing moves', () => {
  for (const r of [0, -1, NaN, undefined, null]) assert.equal(pulseSeconds(r), null, String(r));
});

test('the pulse gets faster as the rate rises, and stays between the slowest and the fastest reading', () => {
  const rates = [0.1, 1, 6, 12, 60, 600, 6000, 1e6];
  const s = rates.map(pulseSeconds);
  for (let i = 1; i < s.length; i++) assert.ok(s[i] <= s[i - 1], `${rates[i]} rpm is not slower than ${rates[i - 1]}`);
  assert.ok(s.every(v => v >= FASTEST && v <= SLOWEST));
  assert.equal(s.at(-1), FASTEST);
  assert.ok(pulseSeconds(0.1) > 3 && pulseSeconds(60) < 1.2);  // 6 a minute reads as a slow beat, 60 as a steady stream
});

test('the red error dash keeps the pulse ground speed: duration scales with its longer period', () => {
  const p = 1.5;
  assert.ok(Math.abs(ERR_PERIOD / errSeconds(p) - PULSE_PERIOD / p) < 1e-9);
});

test('an idle wire is the base and one dashed stroke; a live one adds halo, core and pulse; errors add the red dash', () => {
  const d = 'M0 8 L79 8';
  assert.deepEqual(wireLayers({ d }).map(l => l.cls), ['wire-base', 'wire-idle']);
  assert.deepEqual(wireLayers({ d, rpm: 12 }).map(l => l.cls), ['wire-base', 'wire-halo', 'wire-core', 'wire-pulse']);
  const e = wireLayers({ d, rpm: 12, errors: 3 });
  assert.deepEqual(e.map(l => l.cls), ['wire-base', 'wire-halo', 'wire-core', 'wire-pulse', 'wire-err']);
  assert.ok(e.every(l => l.d === d));
  assert.ok(parseFloat(e[4].style['--dur']) > parseFloat(e[3].style['--dur']));
  assert.deepEqual(wireLayers({ d, rpm: 0, errors: 5 }).map(l => l.cls), ['wire-base', 'wire-idle']);  // errors on a dead wire are not drawn as motion
});

test('every wire says its state in words, so colour and motion are never the only carrier', () => {
  assert.equal(wireLabel({ plane: 'access', rpm: 12, errors: 2, label: 'Login to slurmctld' }), 'Login to slurmctld: Access wire, 12 requests a minute, 2 errors');
  assert.equal(wireLabel({ plane: 'data' }), 'Data wire, no current');
  assert.equal(wireLabel({ plane: 'k8s', rpm: 1.26, errors: 1 }), 'Kubernetes control wire, 1.3 requests a minute, 1 error');
});

test('there are six planes, as in the brief', () => assert.deepEqual(PLANES, ['access', 'control', 'k8s', 'data', 'monitoring', 'management']));
