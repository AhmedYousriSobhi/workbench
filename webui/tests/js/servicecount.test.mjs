import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { servicesCount } from '../../static/js/servicecount.js';
import { attentionItems, homeSummary } from '../../static/js/homemodel.js';

const seven = { 'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'gpu-exporter': true, teleport: true, jump: true, controller: true };

test('every drawn part counts once: 7 parts answering read 7/7', () => {
  const c = servicesCount(seven);
  assert.deepEqual([c.up, c.all, c.ratio, c.words, c.stackDown, c.down], [7, 7, '7/7', '7 of 7', false, []]);
});
test('one part down is 6/7 and named; all down is the stack down', () => {
  assert.deepEqual(servicesCount({ ...seven, jump: false }).down, ['jump']);
  assert.equal(servicesCount({ ...seven, jump: false }).ratio, '6/7');
  assert.equal(servicesCount(Object.fromEntries(Object.keys(seven).map(k => [k, false]))).stackDown, true);
});
test('no parts at all is 0/0 and not a stack down', () => {
  for (const x of [undefined, null, {}]) assert.deepEqual([servicesCount(x).ratio, servicesCount(x).stackDown], ['0/0', false]);
});
test('header line and attention list read the same count and the same down parts', () => {
  const cl = { nodes: [], services: { ...seven, jump: false, controller: false } };
  assert.match(homeSummary(cl), /5 of 7 services answering/);
  const down = attentionItems({ level: 0 }, cl, ['n']).map(i => i.text);
  assert.deepEqual(down, ['Service jump is not answering.', 'Service controller is not answering.']);
});
