import { S } from './state.js';
import { $, esc, paintDyn } from './util.js';
import { kindOf } from './svg.js';
import { clusterFacts } from './overview.js';
import { paintHealth } from './governor.js';
import { showNode } from './node.js';
import { openConnection } from './inspector.js';
import { paintBlocks, paintTiles } from './diagram.js';
import { paintTraffic } from './traffic.js';


export const STATE_ICON = { ok: '✓', warn: '▲', fault: '✗', down: '⏻', unknown: '?' };
export function node(n) {
  const util = n.gpu_util == null ? '' : `<span class="meter" role="img" aria-label="GPU utilisation ${n.gpu_util} percent"><i data-w="${n.gpu_util}"></i></span><b>${n.gpu_util}%</b> · ${n.gpus_busy}/${n.gpus} busy · `;
  const info = util + [n.temp == null ? null : n.temp.toFixed(0) + ' °C', n.bcm ? 'BCM ' + n.bcm : null].filter(Boolean).join(' · ') || esc(n.power ?? '–');
  return `<li><button class="er node ${n.state}" data-node="${esc(n.name)}" aria-label="${esc(n.name)}: ${esc(n.label)}. Open details and fault actions">
    <b class="g" aria-hidden="true">${STATE_ICON[n.state]}</b><span class="nm">${esc(n.name)}</span><span class="st">${esc(n.label)}</span><span class="info">${info}</span></button></li>`;
}
export function svcRow([k, up]) {
  return `<li><div class="er svc ${up ? 'ok' : 'fault'}"><b class="g" aria-hidden="true">${up ? '✓' : '✗'}</b><span class="nm">${esc(k)}</span><span class="st">${up ? 'answering' : 'not answering'}</span><span class="info" id="si-${esc(k)}"></span></div></li>`;
}
export function paintKpis() {  // stack down: no cards, one notice (W-10); the cluster at a glance, all from the page's own data (nodes, reachability, applied faults); nothing is invented
  const f = clusterFacts(), gpu = f.nodes.filter(n => kindOf(n) === 'gpu'), gt = gpu.reduce((a, n) => a + (n.gpus || 0), 0), gb = gpu.reduce((a, n) => a + (n.gpus_busy || 0), 0);
  const bad = f.nodes.length - f.ok, k = (cls, v, of, label) => `<div class="kpi ${cls}"><b>${v}${of == null ? '' : ` <small>of ${of}</small>`}</b><span>${label}</span></div>`;
  $('kpis').innerHTML = !f.nodes.length || f.off ? '' : k(bad ? 'warn' : 'ok', f.ok, f.nodes.length, 'nodes healthy') + k(f.off ? 'bad' : f.up < f.svcs.length ? 'warn' : 'ok', f.up, f.svcs.length, 'services up')
    + (gt ? k('', gb, gt, 'GPUs busy') : '') + k(f.faults ? 'bad' : 'ok', f.faults, null, f.faults === 1 ? 'fault applied' : 'faults applied');
}
export function renderCluster(c) {
  if (S.activeCluster && c.clusters) { const m = c.clusters.find(x => x.name === S.activeCluster); if (m) c = { ...c, ...m }; }  // several clusters (#327): the list and Connections follow the cluster entered
  paintHealth(c);
  if ($('flow').firstChild) paintTiles();
  setTimeout(paintKpis);
  const stackUp = Object.values(c.services).some(Boolean);
  $('stackdown').hidden = stackUp;
  $('nodeswrap').classList.toggle('solo', !stackUp);
  for (const id of ['nodesmain']) $(id).hidden = !stackUp;  // one message when down, not five
  const svcs = Object.entries(c.services), svcUp = svcs.filter(([, u]) => u).length;
  $('services').innerHTML = svcs.map(svcRow).join(''); $('cnt-svc').textContent = `${svcUp} of ${svcs.length} answering`; paintBlocks();
  for (const kind of ['compute', 'gpu', 'k8s']) {
    const rows = c.nodes.filter(n => kindOf(n) === kind);
    $('nodes-' + kind).innerHTML = rows.map(n => node(n)).join(''); paintDyn($('nodes-' + kind)); $('br-' + kind).hidden = !rows.length;
    $('cnt-' + kind).textContent = `${rows.filter(n => n.state === 'ok').length} of ${rows.length} healthy`;
  }
  document.querySelectorAll('.node').forEach(b => b.onclick = () => { const n = c.nodes.find(x => x.name === b.dataset.node); if (n && n.kind === 'k8s') openConnection('k8s'); else showNode(b.dataset.node); });  // a Kubernetes node has no BMC or Slurm actions: its row is Connections
  if (S.openNode) { const n = c.nodes.find(x => x.name === S.openNode); if (n) { $('nodestate').className = 'pill ' + n.state; $('nodestate').textContent = `${STATE_ICON[n.state]} ${n.label}`; } }
}
let paused = false;
export function pauseCluster() { paused = true; clearTimeout(S.clusterTimer); }  // hidden page: no polling (W-11, U2)
export function resumeCluster() { if (paused) { paused = false; loadCluster(); } }  // only if it was paused: a show event alone does not re-poll
export async function loadCluster() {
  clearTimeout(S.clusterTimer);
  try {
    const text = await (await fetch('/api/cluster')).text();
    if (text !== S.lastCluster) { S.lastCluster = text; renderCluster(JSON.parse(text)); paintTraffic(); }  // the Traffic view follows node faults (issue 414)
  } catch (e) { /* keep the last view; the next poll retries */ }
  if (!document.hidden) S.clusterTimer = setTimeout(loadCluster, 4000);  // a hidden page does not poll (W-11, U2); showing it polls at once
}
