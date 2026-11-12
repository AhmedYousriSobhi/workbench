// Cluster setup, "Review changes" (screen 4, U6) as data. Pure, so `node --test` covers it.
// Honesty rules: only what the page can derive is listed; restarts are limited to the two causes benchctl/stack.py has (the Slurm node list
// changed, accounting added); running jobs are known only from Slurm node states, so a node's jobs are "stop", never a count or ids.

const pad = i => String(i).padStart(3, '0');
export const targetNames = (prefix, count) => Array.from({ length: Math.max(0, count | 0) }, (_, i) => prefix + pad(i + 1));
const by = (nodes, f) => nodes.filter(f).map(n => n.name);
const missing = (a, b) => a.filter(x => !b.includes(x));

// nodes: cluster rows [{name, kind, gpus}]; want: {cpu, gpu, kcpu, kgpu, k8s} (k8s false: every Kubernetes node goes);
// prefixes: {cpu, gpu, kcpu, kgpu}; states: {node: Slurm state} or null when unread; parts*: Sets of part ids; labels: id -> label;
// loginsNow/loginsWant: arrays of names -> {adds, removes, restarts, jobs: {nodes, known}, changed}
export function planDiff({ nodes, want, prefixes, states, partsNow, partsWant, labels = {}, loginsNow = [], loginsWant = [] }) {
  const groups = [
    ['cpu', by(nodes, n => n.kind === 'compute'), want.cpu, true], ['gpu', by(nodes, n => n.kind === 'gpu'), want.gpu, true],
    ['kcpu', by(nodes, n => n.kind === 'k8s' && !n.gpus), want.k8s ? want.kcpu : 0, false], ['kgpu', by(nodes, n => n.kind === 'k8s' && n.gpus > 0), want.k8s ? want.kgpu : 0, false],
  ];
  const adds = [], removes = [], slurmRemoved = []; let slurmChanged = false;
  for (const [g, have, count, slurm] of groups) {
    const target = targetNames(prefixes[g], count), add = missing(target, have), rm = missing(have, target);
    adds.push(...add); removes.push(...rm);
    if (slurm && (add.length || rm.length)) slurmChanged = true;
    if (slurm) slurmRemoved.push(...rm);
  }
  const partName = id => labels[id] || id;
  const pAdd = [...partsWant].filter(p => !partsNow.has(p)), pRm = [...partsNow].filter(p => !partsWant.has(p));
  adds.push(...pAdd.map(partName)); removes.push(...pRm.map(partName));
  const lAdd = missing(loginsWant, loginsNow), lRm = missing(loginsNow, loginsWant);
  adds.push(...lAdd); removes.push(...lRm);
  const restarts = [];
  if (slurmChanged) restarts.push({ what: 'slurmctld', why: 'the node list changed' });
  else if (pAdd.includes('accounting')) restarts.push({ what: 'slurmctld', why: 'accounting was added' });
  if (slurmChanged && pAdd.includes('accounting')) restarts[0].why = 'the node list changed and accounting was added';
  const busy = states ? slurmRemoved.filter(n => ['allocated', 'mixed'].includes(states[n])) : [];
  const jobs = { known: !!states || !slurmRemoved.length, nodes: busy, removedSlurm: slurmRemoved };
  return { adds, removes, restarts, jobs, changed: !!(adds.length || removes.length || restarts.length) };
}

// the review as lines of {icon, label, text} for the sheet: words and an icon, never colour alone
export function reviewLines(d) {
  const lines = [];
  if (!d.changed) return [{ kind: 'none', icon: '✓', label: 'No change', text: 'The plan matches what is running.' }];
  if (d.adds.length) lines.push({ kind: 'add', icon: '+', label: 'Adds', text: d.adds.join(', ') });
  if (d.removes.length) lines.push({ kind: 'remove', icon: '−', label: 'Removes', text: d.removes.join(', ') });
  for (const r of d.restarts) lines.push({ kind: 'restart', icon: '↻', label: 'Restarts', text: `${r.what} (${r.why})` });
  return lines;
}

// the jobs warning: null when nothing to say; `needsAck` blocks Apply until the box is ticked
export function jobsWarning(d) {
  const j = d.jobs;
  if (!j.removedSlurm.length) return null;
  if (!j.known) return { kind: 'unknown', icon: '▲', needsAck: false, title: 'Running jobs: not read', text: `${j.removedSlurm.join(', ')} will be removed. The page could not read Slurm's node states (the governor or the stack is not answering), so it cannot say whether jobs are running there. Removing a node stops its jobs.` };
  if (!j.nodes.length) return { kind: 'none', icon: '✓', needsAck: false, title: 'No running jobs on the nodes to remove', text: `Slurm reports ${j.removedSlurm.join(', ')} with no jobs.` };
  return { kind: 'stop', icon: '✗', needsAck: true, title: `Jobs will stop on ${j.nodes.length} node${j.nodes.length === 1 ? '' : 's'}`, text: `${j.nodes.join(', ')} ${j.nodes.length === 1 ? 'has' : 'have'} running jobs (Slurm state allocated or mixed). Removing a node kills its jobs.` };
}
