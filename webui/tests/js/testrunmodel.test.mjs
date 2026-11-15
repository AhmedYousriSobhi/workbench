import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { describe, buildReport, eyeBase, eyeRows, runVerdict, timelineRows } from '../../static/js/testrunmodel.js';

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
  assert.equal(describe({ do: { tickets: { action: 'transition', to: 'In Progress' } } }), 'Move the matching Jira tickets to In Progress');
});

test('a Kubernetes drain step (an object, not a list) is described, not a crash', () => {
  assert.match(describe({ do: { drain: { node: 'kgpu001', timeout: 30 } } }), /Kubernetes node kgpu001/);
  assert.match(describe({ do: { drain: ['cn001'], reason: 'x' } }), /Drain cn001 in Slurm/);
});

test('every step kind reads as a sentence, not JSON (issue 813)', () => {
  const said = (target, d) => describe({ target, do: d });
  assert.equal(said('ssh', { state: 'down' }), 'Stop the login node (SSH to it fails)');
  assert.equal(said('ssh', { state: 'hung' }), 'Freeze the login node (SSH hangs)');
  assert.equal(said('ssh', { node_sshd: 'down', nodes: ['cn001'] }), 'Stop sshd on cn001 (the node stays up)');
  assert.equal(said('ssh', { max_sessions: 1 }), 'Allow 1 SSH session per connection on the login node');
  assert.equal(said('auth', { teleport_cert_ttl: { user: 'alice', ttl: '5m' } }), "Make alice's Teleport identity expire after 5 min");
  assert.equal(said('auth', { munge_wrong_key: ['cn001'] }), 'Give cn001 a wrong munge key');
  assert.equal(said('auth', { k8s_cert_expired: false }), "Give the Kubernetes API server its valid certificate again");
  assert.equal(said('netem', { nodes: ['cn003'], delay: '200ms', loss: '5%', ttl: '60s' }), 'Add 200 ms delay and 5% packet loss on cn003 for 60 s');
  assert.equal(said('netem', { clear: true }), 'Remove the network delay and loss from every node');
  assert.equal(said('switch', { leaf: 'leaf1', state: 'down' }), 'Take leaf switch leaf1 down (its nodes drop out)');
  assert.equal(said('k8s', { down: ['kgpu001'] }), 'Take Kubernetes node kgpu001 down (NotReady)');
  assert.equal(said('k8s', { pod: { namespace: 'team-b', name: 'p', fault: 'crashloop' } }), 'Start pod team-b/p that crash-loops');
  assert.equal(said('k8s', { queue: { name: 'q', active: false, pending: 2 } }), 'Stop queue q admitting work (2 workloads pending)');
  assert.equal(said('jira-real', { fault: 'proxy_503', count: 1 }), "Make the real Jira's proxy answer 503 for the next call (Retry-After 30 s)");
  assert.equal(said('jira-real', { tickets: { action: 'create', count: 2, summary: 'x' } }), 'Create 2 real Jira tickets: x');
  assert.equal(said('slurm-upgrade', { to: '26.05.5', daemons: ['login'] }), 'Upgrade the login node to Slurm 26.05.5');
  assert.equal(said('slurm', { reboot: ['cn002'] }), 'Reboot cn002 through Slurm (scontrol reboot ASAP)');
  assert.equal(said('gpu-exporter', { nodes: ['cn001'], storage: { mount: '/scratch', stale: true } }), 'Make /scratch stale on cn001');
  assert.equal(said('gpu-exporter', { nodes: ['gpu001'], ib_down: 0 }), 'Bring the InfiniBand ports back up on gpu001');
  assert.equal(said('bmc-farm', { nodes: ['gpu001'], event: 'Fell off the bus', severity: 'Critical' }), 'BMC event on gpu001 (Critical): Fell off the bus');
  assert.equal(said('dcgm', { nodes: ['gpu001'], restart: true }), 'Restart the real DCGM exporter on gpu001');
  assert.equal(said('jira', { failure: { mode: 'slow', count: 2, delay: 3 } }), 'Make Jira answer 3 s late for the next 2 calls');
  assert.equal(said('nothing-known', { weird: 1 }), '{"weird":1}');  // the last resort, which tests/test_step_words.py keeps off every shipped step
});
