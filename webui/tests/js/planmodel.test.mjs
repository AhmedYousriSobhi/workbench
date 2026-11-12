import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { jobsWarning, planDiff, reviewLines, targetNames } from '../../static/js/planmodel.js';

const nodes = [...['cn001', 'cn002', 'cn003', 'cn004'].map(name => ({ name, kind: 'compute', gpus: 0 })), ...['gpu001', 'gpu002'].map(name => ({ name, kind: 'gpu', gpus: 8 })), { name: 'rgpu001', kind: 'real-gpu', gpus: 1 }];
const prefixes = { cpu: 'cn', gpu: 'gpu', kcpu: 'kcn', kgpu: 'kgpu' };
const base = { nodes, prefixes, states: { cn003: 'allocated', cn004: 'idle', cn001: 'mixed' }, partsNow: new Set(['jira', 'accounting']), partsWant: new Set(['jira', 'accounting']), labels: { teleport: 'Teleport', accounting: 'Accounting' } };
const w = (o = {}) => ({ cpu: 4, gpu: 2, kcpu: 0, kgpu: 0, k8s: false, ...o });

test('names are the prefix plus three digits, in order', () => assert.deepEqual(targetNames('gpu', 3), ['gpu001', 'gpu002', 'gpu003']));
test('the same plan is no change and says so', () => {
  const d = planDiff({ ...base, want: w() });
  assert.equal(d.changed, false); assert.equal(reviewLines(d)[0].label, 'No change'); assert.equal(jobsWarning(d), null);
});
test('adds, removes and the slurmctld restart come from the node list; a real GPU node is left alone', () => {
  const d = planDiff({ ...base, want: w({ cpu: 2, gpu: 4 }), partsWant: new Set(['jira', 'accounting', 'teleport']) });
  assert.deepEqual(d.adds, ['gpu003', 'gpu004', 'Teleport']); assert.deepEqual(d.removes, ['cn003', 'cn004']);
  assert.deepEqual(d.restarts, [{ what: 'slurmctld', why: 'the node list changed' }]);
  assert.deepEqual(reviewLines(d).map(l => l.label), ['Adds', 'Removes', 'Restarts']);
});
test('a Teleport part alone restarts nothing; accounting added restarts slurmctld', () => {
  assert.deepEqual(planDiff({ ...base, want: w(), partsWant: new Set(['jira', 'accounting', 'teleport']) }).restarts, []);
  const d = planDiff({ ...base, want: w(), partsNow: new Set(['jira']), partsWant: new Set(['jira', 'accounting']) });
  assert.deepEqual(d.restarts, [{ what: 'slurmctld', why: 'accounting was added' }]);
});
test('removing a node with an allocated or mixed state means its jobs stop and need an acknowledgement', () => {
  const j = jobsWarning(planDiff({ ...base, want: w({ cpu: 2 }) }));   // removes cn003 (allocated) and cn004 (idle)
  assert.equal(j.kind, 'stop'); assert.equal(j.needsAck, true); assert.match(j.title, /1 node$/); assert.match(j.text, /cn003 has running jobs/);
  assert.equal(jobsWarning(planDiff({ ...base, want: w({ cpu: 3 }) })).kind, 'none');   // only the idle cn004 goes
});
test('unread Slurm states are said, never guessed; no removal means no warning', () => {
  const j = jobsWarning(planDiff({ ...base, states: null, want: w({ cpu: 3 }) }));
  assert.equal(j.kind, 'unknown'); assert.equal(j.needsAck, false); assert.match(j.text, /could not read/);
  assert.equal(jobsWarning(planDiff({ ...base, states: null, want: w({ cpu: 5 }) })), null);
});
test('Kubernetes nodes: turning the part off removes them and does not restart slurmctld; login names diff too', () => {
  const k = [...nodes, { name: 'kcn001', kind: 'k8s', gpus: 0 }, { name: 'kgpu001', kind: 'k8s', gpus: 8 }];
  const d = planDiff({ ...base, nodes: k, want: w(), loginsNow: ['login', 'login2'], loginsWant: ['login'] });
  assert.deepEqual(d.removes, ['kcn001', 'kgpu001', 'login2']); assert.deepEqual(d.restarts, []);
  assert.equal(jobsWarning(d), null);
});
