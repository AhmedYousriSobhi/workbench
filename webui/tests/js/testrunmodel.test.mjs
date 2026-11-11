import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { buildReport, eyeBase, eyeRows, runVerdict, timelineRows } from '../../static/js/testrunmodel.js';

const prev = { name: 'sc', ref: 'SC-09', steps: [
  { at: 0, target: 'slurm', do: { drain: ['cn001'] }, page: true, why: '' },
  { at: 5, target: 'k8s', do: { clear: true, nodes: ['k8s001'] }, page: false, why: 'k8s steps need the host CLI' }], expects: [] };

test('a CLI-only step is marked before anything runs', () => {
  const rows = timelineRows(prev, null);
  assert.equal(rows[0].page, true); assert.equal(rows[1].page, false);
  assert.equal(rows[1].status, 'pending'); assert.match(rows[1].why, /host CLI/);
});
test('run state joins by index; another scenario\'s run is ignored', () => {
  const run = { name: 'sc', steps: [{ status: 'ok', detail: '' }, { status: 'skipped', detail: 'k8s steps need the host CLI (no Docker access here)' }] };
  assert.deepEqual(timelineRows(prev, run).map(r => r.status), ['ok', 'skipped']);
  assert.deepEqual(timelineRows(prev, { ...run, name: 'other' }).map(r => r.status), ['pending', 'pending']);
});
test('a skip is never a clean pass', () => {
  const run = { name: 'sc', ok: true, running: false, steps: [{ status: 'ok' }, { status: 'skipped' }], expects: [] };
  assert.equal(runVerdict(run), 'passed with skips');
  assert.equal(runVerdict({ ...run, steps: [{ status: 'ok' }] }), 'passed');
  assert.equal(runVerdict({ ...run, ok: false }), 'failed'); assert.equal(runVerdict({ ...run, running: true }), 'running'); assert.equal(runVerdict(null), null);
});
test('Client\'s eye: counts during the run, not measured is not zero', () => {
  const base = eyeBase({ totals: { jira: 10, bcm: 4 }, errors: { jira: 1 } });
  const snap = { reachable: { jira: true, bcm: true, k8s: true, dead: false }, totals: { jira: 25, bcm: 4 }, errors: { jira: 3 } };
  const by = Object.fromEntries(eyeRows(snap, base).map(r => [r.key, r]));
  assert.equal(by.jira.during, 15); assert.equal(by.jira.errors, 2); assert.equal(by.jira.verdict, 'called');
  assert.equal(by.bcm.verdict, 'not called yet'); assert.equal(by.bcm.errors, null);
  assert.equal(by.k8s.verdict, 'not measured'); assert.equal(by.k8s.during, null);
  assert.equal(by.dead.verdict, 'not reachable');
  assert.deepEqual(eyeRows(null, base), []);
});
test('without a base the total since the page opened decides the verdict', () => {
  assert.equal(eyeRows({ reachable: { a: true }, totals: { a: 3 } }, null)[0].verdict, 'called');
});
test('the report lists skipped steps as skipped and keeps n/a as n/a', () => {
  const run = { name: 'sc', ok: true, running: false, speed: 5, steps: [{ status: 'ok' }, { status: 'skipped', detail: '' }], expects: [{ label: 'x', status: 'ok', detail: '' }] };
  const { data, markdown } = buildReport({ preview: prev, run, startedAt: '2026-01-01T00:00:00Z', finishedAt: '2026-01-01T00:01:00Z', speed: 5,
    eye: eyeRows({ reachable: { k8s: true }, totals: {} }, null) });
  assert.equal(data.verdict, 'passed with skips'); assert.equal(data.steps[1].status, 'skipped'); assert.equal(data.steps[1].page_could_run, false);
  assert.match(markdown, /Result: \*\*passed with skips\*\*/); assert.match(markdown, /\| k8s \| n\/a \| n\/a \| not measured \|/);
  assert.doesNotMatch(markdown, /\/home\/|token|@/);
});
test('a ticket step reads as words, not JSON', async () => {
  const { describe } = await import('../../static/js/testrunmodel.js');
  assert.equal(describe({ do: { tickets: { action: 'create', count: 4, summary: 'Wave' } } }), 'Create 4 Jira tickets: Wave');
  assert.equal(describe({ do: { tickets: { action: 'transition', to: 'In Progress' } } }), 'Move Jira ticket to In Progress');
});
