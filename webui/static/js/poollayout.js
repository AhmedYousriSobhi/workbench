// The Pool level of the infrastructure zoom (redesign step 4): what the side panel and the tile grid say about one pool of nodes.
// Pure data in, plain objects out (webui/tests/js/poollayout.test.mjs); pool.js draws it.
//
// Honesty: the page knows how many GPUs of a node are busy (the exporter's utilisation) and nothing about CPU, memory, load or
// network of a node. A pool nobody measures says "not measured" and its Busy and Idle filters are off; it never guesses.

export const PER_PAGE = 16;  // a 4 x 4 grid
export const FILTERS = [['all', 'All'], ['busy', 'Busy'], ['idle', 'Idle'], ['attention', 'Needs attention']];
export const STATE_ORDER = ['ok', 'warn', 'fault', 'down', 'unknown'];
export const STATE_WORD = { ok: 'Healthy', warn: 'Warning', fault: 'Fault', down: 'Down', unknown: 'No data' };

// The two filters that need a measurement; a pool where nothing reports one cannot tell busy from idle.
export const measured = nodes => nodes.some(n => n.gpus_busy != null);
export const isBusy = n => n.gpus_busy > 0;
export const FILTER_TEST = {
  all: () => true,
  busy: n => isBusy(n),
  idle: n => n.state === 'ok' && n.gpus_busy === 0,
  attention: n => n.state !== 'ok',
};

export function filterCounts(nodes) {
  const on = measured(nodes), out = {};
  for (const [k] of FILTERS) out[k] = { n: nodes.filter(FILTER_TEST[k]).length, enabled: on || (k !== 'busy' && k !== 'idle') };
  return out;
}

// The side panel's numbers. avgUtil is null when no node reports a utilisation, never a made-up 0.
export function poolKpis(nodes) {
  const utils = nodes.map(n => n.gpu_util).filter(u => typeof u === 'number');
  return {
    total: nodes.length, inService: nodes.filter(n => n.state === 'ok').length, attention: nodes.filter(n => n.state !== 'ok').length,
    gpus: nodes.reduce((s, n) => s + (n.gpus || 0), 0), gpusBusy: nodes.reduce((s, n) => s + (n.gpus_busy || 0), 0),
    avgUtil: utils.length ? Math.round(utils.reduce((a, b) => a + b, 0) / utils.length) : null,
  };
}

// The stacked state bar: one segment per state that occurs, widths summing to 100.
export function stateBar(nodes) {
  const total = nodes.length || 1;
  return STATE_ORDER.map(s => ({ state: s, n: nodes.filter(n => n.state === s).length })).filter(x => x.n).map(x => ({ ...x, pct: Math.round(x.n * 1000 / total) / 10 }));
}

// One page of the tile grid for a filter. The page number is clamped, so a shrinking pool never shows an empty page.
export function pageOf(nodes, filter, page, per = PER_PAGE) {
  const test = FILTER_TEST[filter] || FILTER_TEST.all, list = nodes.filter(test), pages = Math.max(1, Math.ceil(list.length / per)), p = Math.min(Math.max(0, page | 0), pages - 1);
  return { items: list.slice(p * per, (p + 1) * per), page: p, pages, total: list.length };
}

// A tile's GPU bars: at most 8, lit for the busy ones. The page knows how many are busy, not each one's percentage.
export function gpuBars(n) {
  if (!(n.gpus > 0) || n.gpus_busy == null) return null;
  const k = Math.min(8, n.gpus), lit = Math.round(n.gpus_busy * k / n.gpus);
  return { bars: Array.from({ length: k }, (_, i) => i < lit), text: `${n.gpus_busy} of ${n.gpus} GPUs busy` + (typeof n.gpu_util === 'number' ? ` · ${n.gpu_util} % average` : '') };
}

export const POOL_FED = {
  sc: 'slurmctld schedules onto these nodes; the BMC farm reports their hardware.',
  sg: 'slurmctld schedules onto these nodes; the BMC farm and the GPU exporter report them.',
  kc: 'kube-apiserver lists these nodes; KWOK plays their kubelets.',
  kg: 'kube-apiserver lists these nodes; KWOK plays their kubelets, so GPU use is not measured.',
};
