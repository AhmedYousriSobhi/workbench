// Pure logic of the page: no DOM, no network. Anything here that changes behaviour is a deliberate change.
import './setup.mjs';
import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import { S } from '../../static/js/state.js';
import { esc } from '../../static/js/util.js';
import { roundedPoly } from '../../static/js/svg.js';
import { nhcOrderAfterMove } from '../../static/js/nhc.js';
import { describe, plain } from '../../static/js/testrunmodel.js';
import { pathOf, rateOf, trRate, SLURM_OUT } from '../../static/js/traffic.js';
import { withHelperStatus, HELPER_OFF } from '../../static/js/connections.js';

test('esc escapes what could end an HTML text or a double-quoted attribute', () => {
  assert.equal(esc('<b a="1">&</b>'), '&lt;b a=&quot;1&quot;&gt;&amp;&lt;/b&gt;');
  assert.equal(esc(42), '42');
  assert.equal(esc(null), 'null');
});

test('roundedPoly rounds each inner corner with an arc no longer than half a segment', () => {
  assert.equal(roundedPoly([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }]), 'M0 0 L5 0 Q10 0 10 5 L10 10');
  assert.equal(roundedPoly([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }], 16), 'M0 0 L84 0 Q100 0 100 16 L100 100');
  assert.equal(roundedPoly([{ x: 0, y: 0 }, { x: 5, y: 5 }]), 'M0 0 L5 5');
});

test('nhcOrderAfterMove returns the check names with one moved, clamped to the list', () => {
  S.nhcState = { checks: ['a', 'b', 'c'].map(name => ({ name })) };
  assert.deepEqual(nhcOrderAfterMove('a', 2), ['b', 'c', 'a']);
  assert.deepEqual(nhcOrderAfterMove('c', 0), ['c', 'a', 'b']);
  assert.deepEqual(nhcOrderAfterMove('b', 99), ['a', 'c', 'b']);
  assert.deepEqual(nhcOrderAfterMove('b', -5), ['b', 'a', 'c']);
});

test('describe says in one line what a scenario step does', () => {
  assert.equal(describe({ do: { drain: ['cn001', 'cn002'], reason: 'maintenance' } }), 'Drain cn001, cn002 in Slurm (maintenance)');
  assert.equal(describe({ do: { drain: ['cn001'] } }), 'Drain cn001 in Slurm (scenario)');
  assert.equal(describe({ do: { submit: { node: 'gpu001', gpus: 8 } } }), 'Submit a 600 s job on gpu001 holding 8 GPUs');
  assert.equal(describe({ do: { xid: 79, nodes: ['gpu001'] } }), 'GPU error XID 79 on gpu001');
  assert.equal(describe({ do: { failure: { mode: '503', count: 3 } } }), 'Make Jira answer 503 for the next 3 calls');
  assert.equal(describe({ do: { failure: {} } }), 'Jira back to normal');
  assert.equal(describe({ do: { weird: 1 } }), '{"weird":1}');
});

test('plain turns a step failure into something a person can act on', () => {
  assert.equal(plain({}), '');
  assert.equal(plain({ detail: 'host unreachable', target: 'bmc-farm' }), "can't reach bmc-farm: is the stack running?");
  assert.equal(plain({ detail: 'Slurm steps need the CLI' }), 'needs the host CLI');
  assert.equal(plain({ detail: 'other' }), 'other');
});

test('pathOf follows a chain upstream to a source and downstream to a sink, and only that', () => {
  const links = [{ id: 1, from: 'x', to: 'y' }, { id: 2, from: 'y', to: 'z' }, { id: 3, from: 'q', to: 'z' }, { id: 4, from: 'z', to: 'w' }];
  assert.deepEqual([...pathOf(links, 1)].sort(), [1, 2, 4]);
  assert.deepEqual([...pathOf(links, 3)].sort(), [3, 4]);
  assert.deepEqual([...pathOf(links, 99)], [99]);
  assert.doesNotThrow(() => pathOf([{ id: 1, from: 'a', to: 'b' }, { id: 2, from: 'b', to: 'a' }], 1));  // a loop ends
});

test('rateOf turns a growing counter into requests per second, refreshed at most every 3 s', () => {
  for (const k of Object.keys(trRate)) delete trRate[k];
  let now = 1_000_000;
  const t = mock.method(Date, 'now', () => now);
  assert.equal(rateOf('svc', 10), null);          // the first reading has nothing to compare with
  now += 1000; assert.equal(rateOf('svc', 40), null);  // too soon: keeps the last value
  now += 2000; assert.equal(rateOf('svc', 40), 10);    // 30 requests in 3 s
  now += 3000; assert.equal(rateOf('svc', 10), 0);     // a counter that went down (a restart) is not a negative rate
  t.mock.restore();
});

test('SLURM_OUT matches the states that take a node out of service, not the ones that keep it in', () => {
  for (const s of ['drain', 'drained*', 'draining', 'down', 'fail', 'maint', 'unknown', 'future']) assert.ok(SLURM_OUT.test(s), s);
  for (const s of ['idle', 'mixed', 'allocated', 'completing']) assert.ok(!SLURM_OUT.test(s), s);
});

test('the desktop-helper line on "This page" says one thing per condition (#983)', () => {
  const rows = [{ id: 'ui', name: 'This page' }, { id: 'jira', name: 'Jira mock' }];
  assert.deepEqual(withHelperStatus(rows, false), rows);  // neither switch: nothing
  const off = withHelperStatus(rows, true);  // key set, page started without the switch
  assert.equal(off[0].status, HELPER_OFF);
  assert.equal(off[1].status, undefined);
  assert.match(HELPER_OFF, /benchctl stop, then WORKBENCH_DESKTOP_HELPER=1 \.\/scripts\/benchctl start/);
  const on = [{ id: 'ui', status: 'Desktop helper switch on' }, rows[1]];
  assert.equal(withHelperStatus(on, true)[0].status, 'Desktop helper switch on');  // the server's line wins
  assert.equal(rows[0].status, undefined);  // the cached rows are not changed
});
