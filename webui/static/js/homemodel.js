// Overview (screen 1) as data: what needs attention, one card per node, one chip per service. Pure, so `node --test` covers it.
// Honesty rule: a load bar only where a counter backs it (the GPU exporter's utilisation); everything else is words.

const STATE_TEXT = { ok: '✓', warn: '▲', fault: '✗', down: '⏻', unknown: '?' };

// governor status (or null), cluster snapshot (or null), the governor's level names -> [{kind: bad|warn, icon, text, node?}]
export function attentionItems(gov, cl, levelNames) {
  const items = [];
  if (!gov) items.push({ kind: 'bad', icon: '✗', text: 'The governor is not reachable, so memory is not being protected.' });
  else {
    if (gov.level >= 2) { const l = Math.min(gov.level, 5); items.push({ kind: 'bad', icon: '✗', text: `Memory pressure is at level L${l}: ${levelNames[l].toLowerCase()}.` }); }
    (gov.violations || []).slice(0, 5).forEach(v => items.push({ kind: 'warn', icon: '▲', text: 'Security finding: ' + (typeof v === 'string' ? v : JSON.stringify(v)) }));
  }
  if (cl) {
    const svcs = Object.entries(cl.services), down = svcs.filter(([, up]) => !up).map(([k]) => k), stackDown = down.length === svcs.length;
    if (stackDown) items.push({ kind: 'warn', icon: '▲', text: 'The simulation stack is not running. Start it with ./scripts/benchctl start' });
    else {
      down.forEach(k => items.push({ kind: 'bad', icon: '✗', text: `Service ${k} is not answering.` }));
      cl.nodes.filter(n => n.state !== 'ok').forEach(n => items.push({ kind: n.state === 'fault' ? 'bad' : 'warn', icon: n.state === 'fault' ? '✗' : '▲', text: `${n.name}: ${n.label}`, node: n.name }));
    }
  }
  return items;
}

// one node -> the card: name, kind line, state (icon + words), a load bar only when measured, one detail line
export function homeNodeCard(n) {
  const gpu = n.kind === 'gpu' || n.kind === 'real-gpu' || n.gpus > 0, k8s = n.kind === 'k8s';
  const kind = k8s ? 'Kubernetes' : gpu ? `GPU${n.gpus ? ' · ' + n.gpus + ' GPU' + (n.gpus === 1 ? '' : 's') : ''}` : 'CPU';
  const load = n.gpu_util == null ? null : Math.max(0, Math.min(100, Math.round(n.gpu_util)));
  const bits = [];
  if (n.gpu_util != null) bits.push(`${n.gpus_busy ?? 0} of ${n.gpus} GPUs busy`);
  if (n.temp != null) bits.push(Math.round(n.temp) + ' °C');
  if (!bits.length) bits.push(n.power || 'no live reading');
  return { name: n.name, kind, state: n.state, icon: STATE_TEXT[n.state] || '?', label: n.label || n.state, load, detail: bits.join(' · ') };
}

// {service: up} -> [{name, up, icon, text, real}] in the order given, down ones counted by the caller
export function homeServices(services, realSet) {
  return Object.entries(services).map(([name, up]) => ({ name, up: !!up, icon: up ? '✓' : '✗', text: up ? 'answering' : 'not answering', real: realSet.has(name) }));
}

// "how many of how many" for the header line under the title
export function homeSummary(cl) {
  if (!cl) return 'Waiting for the first snapshot…';
  const up = Object.values(cl.services).filter(Boolean).length, all = Object.keys(cl.services).length;
  if (all && !up) return 'The simulation stack is not running.';
  const ok = cl.nodes.filter(n => n.state === 'ok').length;
  return `${ok} of ${cl.nodes.length} nodes healthy · ${up} of ${all} services answering`;
}
