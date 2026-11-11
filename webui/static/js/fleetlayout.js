// The Fleet level of the infrastructure zoom (redesign step 4): one card per cluster, every card with the same structure.
// Pure data in, plain objects out (webui/tests/js/fleetlayout.test.mjs); fleet.js draws it.
import { circuitFacts, rackSquares } from './circuitlayout.js';

const WORST = { fault: 4, down: 3, warn: 2, unknown: 1, ok: 0 };

// The card's health pill: the worst node decides, and the words say why (never colour alone).
export function health(nodes) {
  if (!nodes.length) return { state: 'unknown', text: 'No nodes reported' };
  const bad = nodes.filter(n => n.state !== 'ok'), top = nodes.reduce((a, n) => (WORST[n.state] ?? 1) > (WORST[a] ?? 1) ? n.state : a, 'ok');
  if (!bad.length) return { state: 'ok', text: 'Healthy' };
  return { state: top, text: `${bad.length} of ${nodes.length} ${bad.length === 1 ? 'needs' : 'need'} attention` };
}

// One card from a cluster's own data ({name, nodes, services, logins}); `extra` holds what only the active cluster can say
// (the accounting probe and the request rate), because the page measures one running stack.
export function fleetCard(c, extra = {}) {
  const f = circuitFacts(c, { dbd: !!extra.dbd }), nodes = c.nodes || [], slurm = nodes.filter(n => n.kind !== 'k8s'), k8s = nodes.filter(n => n.kind === 'k8s');
  return {
    name: c.name, health: health(nodes),
    badges: [
      { k: 'slurm', label: 'Slurm', on: f.slurm },
      { k: 'k8s', label: 'Kubernetes', on: f.k8s },
      { k: 'ctl', label: f.ctl ? 'controller' : 'no controller', on: f.ctl, dashed: !f.ctl },
      { k: 'dbd', label: extra.known ? (f.dbd ? 'accounting' : 'no accounting') : 'accounting not probed', on: !!extra.known && f.dbd, dashed: !f.dbd },
    ],
    kpis: { inService: nodes.filter(n => n.state === 'ok').length, total: nodes.length, attention: nodes.filter(n => n.state !== 'ok').length, rpm: extra.rpm ?? null },
    lanes: [
      { k: 'slurm', title: 'Slurm', present: f.slurm, ...rackSquares(slurm), count: slurm.length },
      { k: 'k8s', title: 'Kubernetes', present: f.k8s, ...rackSquares(k8s), count: k8s.length },
    ],
  };
}

// The cards for the page's snapshot: its `clusters` list when there are several, otherwise the one cluster.
export function fleetCards(snap, activeExtra) {
  const list = snap.clusters && snap.clusters.length ? snap.clusters : [{ ...snap, name: snap.name || 'workbench' }];
  const single = !(snap.clusters && snap.clusters.length);
  return list.map(c => fleetCard(c, single ? activeExtra : { known: false }));
}
