// Where the architecture diagram's lines end and where their labels sit (issue 388).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { channelXs, groupRows, labelFrac, pickChannel, polyFrac, ruleDrop, sweepXs } from '../../static/js/diagramroute.js';

const items = [['label', 'compute', 54], ['node', {}, 24, 80], ['label', 'gpu', 200], ['label', 'k8s', 300]];

test('a label is centred in the gap between two frames, kept off the line ends', () => {
  assert.equal(labelFrac(100, 300, 200), 0.5);
  assert.equal(labelFrac(100, 300, 101), 0.08);
  assert.equal(labelFrac(100, 300, 999), 0.92);
  assert.equal(labelFrac(5, 5, 9), 0.5);
});

test('a label on one run of a bent line is placed by length', () => {
  const pts = [{ x: 0, y: 0 }, { x: 0, y: 10 }, { x: 30, y: 10 }, { x: 30, y: 20 }];
  assert.equal(polyFrac(pts, 1, 0.5), (10 + 15) / 50);
});

test('the Slurm group is the first of compute and GPU, the Kubernetes group its own; the rule sits above the title', () => {
  const g = groupRows(items, 1000, 1);
  assert.equal(g.slurm.label, 'compute');
  assert.equal(g.k8s.label, 'k8s');
  assert.ok(g.slurm.rule < g.slurm.title && g.k8s.rule < g.k8s.title);
  assert.equal(groupRows([['label', 'gpu', 40]], 0, 1).slurm.label, 'gpu');
  assert.deepEqual(groupRows([['label', 'compute', 40]], 0, 1).k8s, undefined);
  assert.ok(ruleDrop(2) < ruleDrop(1));  // a bigger title needs more room
});

test('channels run between tile columns, never through a tile', () => {
  const chs = channelXs(10, 3, 100, 14);
  assert.deepEqual(chs, [10 + 24 + 114 - 7, 10 + 24 + 228 - 7, 10 + 24 + 342 - 7]);
  assert.ok(chs.every(x => ((x - 34 + 7) % 114) === 0));
});

test('a line takes the free channel nearest to where it wants to be, right of the titles, inside its box', () => {
  const chs = [100, 200, 300, 400];
  assert.equal(pickChannel(chs, 210, {}), 200);
  assert.equal(pickChannel(chs, 210, { used: [200] }), 300);  // 200 is taken: 300 (distance 90) beats 100 (110)
  assert.equal(pickChannel(chs, 110, { minX: 150 }), 200);
  assert.equal(pickChannel(chs, 110, { lo: 90, hi: 150 }), 100);
  assert.equal(pickChannel(chs, 110, { avoid: [104] }), 200);
  assert.equal(pickChannel(chs, 110, { lo: 120, hi: 150 }), null);  // none: the caller jogs or gives up
});

test('a sweep gives candidate xs over a span', () => {
  assert.deepEqual(sweepXs(0, 10, 5), [0, 5, 10]);
  assert.equal(pickChannel(sweepXs(0, 100), 50, { avoid: [50] }), 40);
});
