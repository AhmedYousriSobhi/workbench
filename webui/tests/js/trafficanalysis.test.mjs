import './setup.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { streamKey, sampleOf, rateFromSamples, mergeHist, histPercentile, bandPx, riverModel, probeReading, detectPeriods, findAnomalies, RETRY_S, wireHash, parseWireHash, windowRecent, perSecond, wireFacts } from '../../static/js/trafficanalysis.js';

const EDGES = [5, 10, 25, 50, 100, 250, 500, 1000];
const stream = (o = {}) => ({ who: 'curl', service: 'jira-mock', shape: 'GET /rest/api/2/serverInfo', n: 100, ok: 100, client_error: 0, server_error: 0, ms: 300, hist: [90, 8, 2, 0, 0, 0, 0, 0, 0], ...o });
let seed = 7; const rnd = () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;

test('a rate needs two samples 5 s apart, ignores old samples and says null after a restart', () => {
  const s = stream(), at = (t, n) => ({ t, n: { [streamKey(s)]: n } });
  assert.equal(rateFromSamples([at(0, 10)], streamKey(s), 0), null);
  assert.equal(rateFromSamples([at(0, 10), at(3, 15)], streamKey(s), 3), null);
  assert.equal(rateFromSamples([at(0, 10), at(30, 25)], streamKey(s), 30), 30);
  assert.equal(rateFromSamples([at(0, 10), at(100, 90), at(130, 100)], streamKey(s), 130, 60), 20);  // only the last two are inside the window
  assert.equal(rateFromSamples([at(0, 50), at(30, 3)], streamKey(s), 30), null);
  assert.deepEqual(sampleOf(5, [s]), { t: 5, n: { [streamKey(s)]: 100 } });
});

test('a percentile is a bucket edge, never an exact figure, and infinity above the last edge', () => {
  const h = [50, 30, 10, 5, 3, 1, 1, 0, 0];
  assert.deepEqual(histPercentile(h, EDGES, 0.5), { bucket: 0, ms: 5 });
  assert.deepEqual(histPercentile(h, EDGES, 0.95), { bucket: 3, ms: 50 });
  assert.deepEqual(histPercentile([0, 0, 0, 0, 0, 0, 0, 0, 4], EDGES, 0.5), { bucket: 8, ms: Infinity });
  assert.equal(histPercentile([0, 0], [5], 0.5), null);
  assert.deepEqual(mergeHist([stream({ hist: [1, 2, 3] }), stream({ hist: [4, 5, 6] })]), [5, 7, 9]);
});

test('band width follows the square root of the rate and is zero for nothing', () => {
  assert.equal(bandPx(100, 100, 40), 40); assert.equal(bandPx(25, 100, 40), 20); assert.equal(bandPx(0, 100, 40), 0);
  assert.equal(bandPx(null, 100, 40), 0); assert.equal(bandPx(5, 0, 40), 0);
});

test('the river joins callers to services to outcomes, and slow answers come out of the ok share', () => {
  const a = stream({ n: 100, ok: 80, client_error: 5, server_error: 15, hist: [60, 20, 10, 5, 3, 1, 1, 0, 0] });
  const b = stream({ who: 'prometheus', service: 'bmc-farm', shape: 'GET /x', n: 50, ok: 50, hist: [50, 0, 0, 0, 0, 0, 0, 0, 0] });
  const m = riverModel([a, b], EDGES, { [streamKey(a)]: 60, [streamKey(b)]: 30 });
  assert.equal(m.measured, true);
  assert.deepEqual(m.calls.map(c => [c.who, c.service, c.rpm]), [['curl', 'jira-mock', 60], ['prometheus', 'bmc-farm', 30]]);
  assert.equal(m.answers['jira-mock'], 60);
  const total = Object.values(m.outcome).reduce((x, y) => x + y, 0);
  assert.ok(Math.abs(total - 90) < 1e-9, 'the outcomes add up to the rate');
  assert.ok(Math.abs(m.outcome.server_error - 9) < 1e-9 && m.outcome.slow > 0 && m.outcome.ok > 0);
  assert.equal(riverModel([a], EDGES, {}).measured, false);  // no rate yet: nothing drawn as zero
});

test('the probe gives a reading per mode and null where nothing is measured', () => {
  const s = stream({ n: 40, ok: 30, client_error: 4, server_error: 6, hist: [20, 10, 5, 3, 2, 0, 0, 0, 0] });
  assert.equal(probeReading('Rate', s, { rpm: null }).big, null);
  assert.equal(probeReading('Rate', s, { rpm: 12.34 }).big, 12.3);
  assert.equal(probeReading('Delay', s, { edges: EDGES }).big, 5);
  assert.equal(probeReading('Errors', s).big, 25);
  assert.equal(probeReading('Rhythm', s, { periods: [] }).big, null);
  assert.equal(probeReading('Rhythm', s, { periods: [{ seconds: 5.04 }] }).big, 5);
  assert.match(probeReading('Path', s, { callers: ['curl', 'x'] }).verdict, /GET on jira-mock, asked by curl, x/);
  assert.equal(probeReading('Nope', s), null);
});

test('a 5 s timer is found in a jittery stream, a random one is not, and 30 s is not reported as a second timer', () => {
  const timer = Array.from({ length: 60 }, (_, i) => i * 5 + (rnd() - 0.5) * 0.2);
  const p = detectPeriods(timer);
  assert.ok(p.length >= 1 && Math.abs(p[0].seconds - 5) < 0.3 && p[0].strength > 0.5, JSON.stringify(p));
  assert.ok(!p.some(q => Math.abs(q.seconds - 30) < 1), 'a multiple of the period is the same timer');
  let t = 0; const random = Array.from({ length: 60 }, () => (t += -Math.log(1 - rnd()) * 5));
  assert.deepEqual(detectPeriods(random), []);
  assert.deepEqual(detectPeriods([1, 2, 3]), []);
  assert.deepEqual(detectPeriods(Array(20).fill(5)), []);
});

test('each stream is analysed on its own, so two timers on two streams are two answers', () => {
  const a = Array.from({ length: 60 }, (_, i) => i * 5), b = Array.from({ length: 40 }, (_, i) => 1.3 + i * 7.5);
  assert.ok(Math.abs(detectPeriods(a)[0].seconds - 5) < 0.3);
  assert.ok(Math.abs(detectPeriods(b)[0].seconds - 7.5) < 0.4);
});

test('anomalies: no retry after server errors, calls fired together, an unidentified timer', () => {
  const row = (t, who, shape, code) => [t, who, 'jira-mock', shape, code, 5];
  const noRetry = [row(0, 'curl', 'GET /a', 503), row(100, 'curl', 'GET /a', 503), row(200, 'curl', 'GET /a', 503)];
  assert.equal(findAnomalies(noRetry)[0].kind, 'no-retry');
  const retried = [row(0, 'curl', 'GET /a', 503), row(1, 'curl', 'GET /a', 200), row(100, 'curl', 'GET /a', 503), row(101, 'curl', 'GET /a', 200), row(200, 'curl', 'GET /a', 503), row(201, 'curl', 'GET /a', 200)];
  assert.deepEqual(findAnomalies(retried), []);
  assert.ok(RETRY_S > 1);
  const burst = []; for (const t of [10, 20, 30]) for (const p of ['/a', '/b', '/c', '/d']) burst.push(row(t + 0.1, 'x', 'GET ' + p, 200));
  assert.equal(findAnomalies(burst).find(f => f.kind === 'burst').kind, 'burst');
  const f = findAnomalies([], { 'unknown|jira-mock|GET /z': [{ seconds: 30, strength: 0.8 }], 'curl|jira-mock|GET /y': [{ seconds: 5, strength: 0.9 }] });
  assert.deepEqual(f.map(x => x.kind), ['timer']);
  assert.match(f[0].text, /unidentified caller/);
});

test('the wire hash is plain text and round-trips, and a stale one is not a wire', () => {
  const nodes = ['cn001', 'gpu003'];
  assert.equal(wireHash('cluster-a', 'gpu003'), 'cluster-a-gpu003'); assert.equal(wireHash(null, 'gpu003'), 'gpu003');
  assert.deepEqual(parseWireHash('#cluster-a-gpu003', ['cluster-a', 'cluster-b'], nodes), { cluster: 'cluster-a', node: 'gpu003' });
  assert.deepEqual(parseWireHash('gpu003', ['workbench'], nodes), { cluster: 'workbench', node: 'gpu003' });
  assert.equal(parseWireHash('gpu003', ['a', 'b'], nodes).cluster, null);  // several clusters and no cluster named: the node alone
  for (const bad of ['', '#', '#nope', '#cluster-c-gpu003x', null]) assert.equal(parseWireHash(bad, ['cluster-a'], nodes), null, String(bad));
  assert.equal(parseWireHash('#cluster-z-gpu003', ['cluster-a'], nodes), null);
});

test('the window keeps the newer requests and the trace counts one stream per second', () => {
  const rows = [[90, 'a', 's', 'GET /x', 200, 1], [99.5, 'a', 's', 'GET /x', 200, 1], [99.9, 'b', 's', 'GET /x', 200, 1], [20, 'a', 's', 'GET /x', 200, 1]];
  assert.equal(windowRecent(rows, 100, 60).length, 3); assert.equal(windowRecent(rows, 100, Infinity).length, 4);
  const bins = perSecond(rows, 'a|s|GET /x', 100, 30);
  assert.equal(bins.length, 30); assert.equal(bins[29], 1); assert.equal(bins[19], 1); assert.equal(bins.reduce((x, y) => x + y, 0), 2);
});

test('a wire says only what is documented and that its messages are not captured', () => {
  assert.deepEqual(wireFacts({ kind: 'gpu' }).links.map(l => l.to), ['BMC farm', 'slurmctld']);
  assert.deepEqual(wireFacts({ kind: 'k8s' }).links.map(l => l.to), ['KWOK']);
  assert.match(wireFacts({ kind: 'compute' }).capture, /Not captured/); assert.equal(wireFacts(null), null);
});
