// The Cluster level of the infrastructure zoom, drawn (redesign step 3). circuitlayout.js says where things go; this file
// reads the page's cluster data, draws the parts and wires into #flow, and keeps them current without rebuilding.
//
// Honesty rules (webui/graph.py): a wire is pulsing only when a request counter backs it; wiring nothing counts is drawn lit
// but still ("declared"); a part the page cannot probe says "not probed", never "up".
import { S } from './state.js';
import { $, announce, esc } from './util.js';
import { sv } from './svg.js';
import { buildWire, updateWire } from './wire.js';
import { CLIENT_INFO, asButton, clusterNow, nodeList, openPop } from './inspector.js';
import { STATE_ICON } from './cluster.js';
import { fitKey, flowSig, loginAddr, loginRuns, MODE_TEXT, KIND_NAME, loginKind, stackUp } from './geometry.js';
import { trView } from './traffic.js';
import { zoomTo } from './zoom.js';
import { LAYERS, LAYER_OF, LAYER_TEXT, POOLS, CV_W, CV_H, circuitSpec, circuitFacts, layerParts, rackSquares } from './circuitlayout.js';

const CLIENT_CHIPS = ['Monitoring app', 'Prometheus', 'curl · scripts', 'You: ssh · tsh'];
let spec = null, els = {};

export function facts() {
  const c = clusterNow(), mon = S.monLinks || [];
  return circuitFacts(c, { dbd: !!S.dbdLink, monitoring: mon.some(l => l.from === 'prometheus'), grafana: mon.some(l => l.from === 'grafana') });
}

// The link a wire stands for, found by its two ends among the documented links, the monitoring links and the accounting probe.
function linkFor(w, links) {
  if (!w.link) return null;
  const [a, b] = w.link;
  return links.find(l => l.from === a && (b === null ? l.plane === 'monitoring' : l.to === b)) || null;
}
const allLinks = () => [...(clusterNow().comms || []), ...(S.monLinks || []), ...(S.dbdLink ? [S.dbdLink] : [])];

export const recentRate = () => { const r = {}, cut = Date.now() - 60000; for (const e of S.recent) if (e.t > cut) r[e.to] = (r[e.to] || 0) + e.n; return r; };

// What one wire shows right now: requests a minute and errors where a counter backs it, "declared" where nothing does.
export function wireState(w, links, rate) {
  const l = linkFor(w, links), snap = S.flowSnap;
  if (!w.present) return { rpm: 0, errors: 0, declared: false };
  if (w.bus) return { rpm: Object.values(rate).reduce((a, b) => a + b, 0), errors: 0, declared: false };
  if (w.planned) return { rpm: 0, errors: 0, declared: false };
  const svc = w.drop ? w.to : (l && l.service);
  if (l && l.measured && svc) return { rpm: rate[svc] || 0, errors: (snap && snap.errors && snap.errors[svc]) || 0, declared: false, v: trView(l) };
  if (w.drop && snap && w.to in (snap.totals || {})) return { rpm: rate[w.to] || 0, errors: (snap.errors && snap.errors[w.to]) || 0, declared: false };
  return { rpm: 0, errors: 0, declared: true };
}

const T = (cls, x, y, text, extra = {}) => sv('text', { class: cls, x, y, ...extra }, text);

function partText(p, f) {
  const up = stackUp(), lg = p.login;
  switch (p.role) {
    case 'login': return { t: lg ? lg.name + ' · ' + KIND_NAME[loginKind(lg)] : 'login', s: lg ? (loginRuns(lg) ? `✓ ${MODE_TEXT[lg.mode]}` : '✗ not running') : '', m: lg && loginRuns(lg) ? loginAddr(lg) : '', badge: p.head ? 'HEAD NODE · cmsh' : '' };
    case 'klogin': return { t: lg ? (p.via ? `kubectl · via ${lg.name}` : lg.name + ' · k-login') : 'k-login', s: lg ? (loginRuns(lg) ? `✓ ${MODE_TEXT[lg.mode]}` : '✗ not running') : '', m: lg && loginRuns(lg) ? loginAddr(lg) : '' };
    case 'teleport': return { t: 'Teleport proxy', s: p.present ? (S.flowSnap?.reachable.teleport ? '✓ up' : up ? '✗ not reachable' : '– offline') : '', m: ':3080 · certificates' };
    case 'jump': return { t: 'Jump host', s: p.present ? '✓ built' : '', m: 'bare sshd bastion · :2220' };
    case 'slurmctld': return { t: p.ctl ? 'controller · slurmctld' : 'slurmctld', s: p.present ? (S.slurmNodes ? '✓ answers (sinfo)' : up ? '– not probed' : '– offline') : '', m: p.ctl ? 'sshd + cmsh · BCM head node' : 'real · Slurm RPC :6817' };
    case 'slurmdbd': return { t: 'slurmdbd + MariaDB', s: p.present ? '✓ answers (sacctmgr)' : '', m: p.present ? 'real · accounting :6819' : 'accounting · not read' };
    case 'storage': return { t: 'Shared storage', s: '– planned, not built', m: 'NFS or Lustre (SPEC C-11)' };
    case 'k8s-api': return { t: 'kube-apiserver', s: p.present ? (S.flowSnap?.reachable['k8s-api'] ? '✓ up' : up ? '✗ not reachable' : '– offline') : '', m: 'real · :6443' };
    case 'etcd': return { t: 'etcd', s: '', m: 'real · :2379' };
    case 'kwok': return { t: 'KWOK', s: '', m: 'plays the kubelets' };
    case 'service': return { t: p.title, s: '', m: p.sub };
    default: return { t: p.id, s: '', m: '' };
  }
}

function openFor(p) {  // the pop-up each kind of part already has
  if (p.role === 'login' || p.role === 'klogin') return p.login ? () => openPop(els[p.id], 'login', p.login.name) : null;
  if (p.role === 'teleport') return () => openPop(els[p.id], 'svc', 'teleport');
  if (p.role === 'jump' || (p.role === 'slurmctld' && p.ctl)) return () => openPop(els[p.id], 'acc', p.role === 'jump' ? 'jump' : 'controller');
  if (p.role === 'k8s-api') return () => openPop(els[p.id], 'svc', 'k8s-api');
  if (p.role === 'service' && S.flowSnap && p.id in (S.flowSnap.reachable || {})) return () => openPop(els[p.id], 'svc', p.id);
  return null;
}

function rackEl(p, f) {
  const nodes = f.pools[p.pool], [title, lane] = POOLS[p.pool], { per, squares } = rackSquares(nodes);
  const g = sv('g', { class: 'cv-rack', 'data-pool': p.pool, 'data-ent': lane === 'Slurm' ? 'nodes' : 'k8s-nodes' }), cols = Math.floor((p.w - 32) / 18);
  g.append(sv('rect', { class: 'cv-box', x: p.x, y: p.y, width: p.w, height: p.h, rx: 12 }), T('cv-k', p.x + 16, p.y + 24, `${lane === 'Slurm' ? 'SLURM' : 'K8S'} · ${title.toUpperCase()}`));
  squares.forEach((q, i) => {
    const x = p.x + 16 + (i % cols) * 18, y = p.y + 40 + Math.floor(i / cols) * 18, bad = q.state !== 'ok';
    const sq = sv('g', { class: 'cv-sq ' + q.state, 'data-names': q.names.join(' ') }), first = q.names[0];
    sq.append(sv('title', {}, per === 1 ? `${first}: ${q.label}` : `${first} to ${q.names.at(-1)}: ${q.label}`), sv('rect', { x, y, width: 14, height: 14, rx: 3 }));
    if (bad) sq.append(sv('text', { class: 'cv-glyph', x: x + 7, y: y + 11, 'text-anchor': 'middle' }, STATE_ICON[q.state] || '?'));  // shape and glyph, not colour alone
    if (per === 1) { sq.setAttribute('data-ent', first); sq.addEventListener('click', e => { e.stopPropagation(); openPop(sq, 'node', first); }); }
    g.append(sq);
  });
  if (nodes.length) {  // the rack opens its pool: the next level of the zoom
    const open = asButton(sv('g', { 'data-ent': lane === 'Slurm' ? 'nodes' : 'k8s-nodes' }), `Open the ${lane} ${title}: ${nodes.length} nodes`, () => zoomTo('pool', { pool: p.pool, origin: g }), () => [`${lane} ${title}`, 'Zoom in to the nodes of this pool']);
    open.append(sv('rect', { class: 'cv-open', x: p.x + p.w - 124, y: p.y + 8, width: 112, height: 24, rx: 12 }), T('cv-opentext', p.x + p.w - 68, p.y + 25, `${nodes.length} node${nodes.length === 1 ? '' : 's'} ›`, { 'text-anchor': 'middle' })); g.append(open);
  }
  const sum = T('cv-sub cv-sum', p.x + 16, p.y + p.h - 14, '');
  g.append(sum); els[p.id + ':sum'] = sum; els[p.id] = g;
  if (per > 1) g.append(T('cv-sub', p.x + p.w - 16, p.y + p.h - 14, 'one square = 8 nodes', { 'text-anchor': 'end' }));
  return g;
}

function boxEl(p, f) {
  const x = p.x, y = p.y, tx = partText(p, f);
  const g = sv('g', { class: 'cv-part' + (p.role === 'service' ? ' svc' : ''), 'data-ent': entOf(p) });
  g.append(sv('rect', { class: 'cv-box', x, y, width: p.w, height: p.h, rx: 12 }), T('cv-title', x + 14, y + 26, tx.t), T('cv-state', x + 14, y + 48, tx.s, { 'data-k': 's' }), T('cv-sub cv-mono', x + 14, y + 66, tx.m, { 'data-k': 'm' }));
  if (tx.badge) { g.append(sv('rect', { class: 'cv-badge-bg', x: x + p.w - 150, y: y + 8, width: 138, height: 18, rx: 9 }), T('cv-badge', x + p.w - 81, y + 21, tx.badge, { 'text-anchor': 'middle' })); }
  els[p.id] = g;
  const act = openFor(p);
  return act ? asButton(g, `${tx.t}${tx.s ? ': ' + tx.s : ''}. Details`, act) : g;
}
// the id of the part in the declared graph (webui/graph.py), which focus, blast radius and the lens read
function entOf(p) {
  if (p.role === 'login' || p.role === 'klogin') return p.login ? p.login.name : p.id;
  if (p.role === 'slurmctld') return p.ctl ? 'controller' : p.id;
  return p.id;
}

export function buildCircuit() {
  const g = $('flow'), f = facts(), nodes = nodeList();
  spec = circuitSpec(f); els = {};
  g.innerHTML = ''; delete g.dataset.ov; g.dataset.cv = '1'; g.removeAttribute('data-fit'); g.style.removeProperty('--u'); g.style.removeProperty('height');
  S.dots = []; S.tiles = {}; S.svcPos = {}; S.svcOut = {}; S.svcLane = {}; S.flowNodes = flowSig(nodes); S.flowFitKey = fitKey(); $('ovtable').innerHTML = '';
  g.setAttribute('viewBox', `0 0 ${CV_W} ${CV_H}`);
  for (const [k, b] of Object.entries(spec.bands)) {
    const band = sv('g', { class: 'cv-band' + (b.present ? '' : ' absent'), 'data-lane': k });
    band.append(sv('rect', { x: b.x, y: b.y, width: b.w, height: b.h, rx: 16 }), T('cv-k', b.x + 16, b.y + 22, k === 'slurm' ? 'SLURM LANE' : 'KUBERNETES LANE'));
    if (!b.present) band.append(T('cv-note', b.x + b.w / 2, b.y + b.h / 2, b.note, { 'text-anchor': 'middle' }));
    g.append(band);
  }
  for (const [x, t] of [[24, 'CLIENTS'], [310, 'ACCESS'], [640, 'CONTROL'], [1000, 'POOLS'], [1400, 'OPTIONAL · PLANNED']]) g.append(T('cv-k', x, 28, t));
  const wl = sv('g', { class: 'cv-wires' }); g.append(wl);
  spec.wires.forEach(w => { const el = buildWire({ d: w.d, plane: w.plane, rpm: 0, label: w.label || w.key, declared: false }); el.dataset.key = w.key; el.dataset.layer = LAYER_OF[w.plane]; el.dataset.present = w.present; el.setAttribute('data-edge', w.edge || 'cv:' + w.key); w.el = el; wl.append(el); });
  const elab = sv('g', { class: 'cv-labels' });
  for (const w of spec.wires) if (w.label && !w.drop && !w.short) { const m = w.bus ? { x: 700, y: 846 } : midpoint(w.d); elab.append(T('cv-wlab', m.x, m.y - 6, w.label, { 'text-anchor': 'middle', 'data-key': w.key, 'data-present': w.present })); }
  g.append(elab);
  for (const p of Object.values(spec.parts)) {
    if (p.id === 'clients') { g.append(clientsEl(p)); continue; }
    const el = p.role === 'rack' ? rackEl(p, f) : boxEl(p, f);
    el.dataset.present = p.present; el.dataset.part = p.id; g.append(el);
  }
  paintCircuit(); applyLayer();
}
function midpoint(d) { const n = d.match(/-?\d+/g).map(Number), pts = []; for (let i = 0; i < n.length; i += 2) pts.push({ x: n[i], y: n[i + 1] }); const a = pts[Math.floor((pts.length - 1) / 2)], b = pts[Math.ceil((pts.length - 1) / 2)]; return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }

function clientsEl(p) {
  const g = sv('g', { class: 'cv-part cv-clients', 'data-ent': 'clients', 'data-part': 'clients', 'data-present': 'true' });
  g.append(sv('rect', { class: 'cv-box', x: p.x, y: p.y, width: p.w, height: p.h, rx: 14 }), T('cv-title', p.x + 16, p.y + 30, 'Clients'), T('cv-sub', p.x + 16, p.y + 50, 'what a user or tool runs', {}));
  CLIENT_CHIPS.forEach((t, i) => {
    const cg = asButton(sv('g', { 'data-ent': 'clients' }), `${t}: how it connects`, () => openPop(cg, 'client', t), () => [t, CLIENT_INFO[t]]), y = p.y + 72 + i * 56;
    cg.append(sv('rect', { class: 'cv-chip', x: p.x + 14, y, width: p.w - 28, height: 42, rx: 9 }), T('cv-chiptext', p.x + 28, y + 26, t)); g.append(cg);
  });
  els.clients = g; return g;
}

const rackSummary = (nodes, lane) => {
  if (!nodes.length) return 'not in this cluster';
  const bad = nodes.filter(n => n.state !== 'ok');
  return bad.length ? `▲ ${bad.length} ${lane === 'k8s' ? 'NotReady or out' : 'out of service'}: ${bad.slice(0, 2).map(n => `${n.name} ${n.label}`).join(', ')}${bad.length > 2 ? ' …' : ''}` : `✓ all ${nodes.length} in service`;
};

// Everything that changes between rebuilds: wires, readouts, squares. A different node list or a different set of parts rebuilds.
export function paintCircuit() {
  if (!spec) return;
  const f = facts(), links = allLinks(), rate = recentRate(), mark = x => x ? '1' : '0';
  // the drawing is built for one set of parts and one node list; a change in either rebuilds it (called from paintTiles)
  for (const w of spec.wires) {
    const st = wireState(w, links, rate), l = linkFor(w, links);
    updateWire(w.el, { d: w.d, plane: w.present ? w.plane : 'control', rpm: st.rpm, errors: st.errors, declared: st.declared, label: (w.label || w.key) + (l ? ` (${l.protocol} :${l.port})` : '') });
    w.el.setAttribute('data-measured', mark(l && l.measured && !st.declared));
  }
  for (const p of Object.values(spec.parts)) {
    const el = els[p.id]; if (!el) continue;
    if (p.role === 'rack') { const nodes = f.pools[p.pool]; els[p.id + ':sum'].textContent = rackSummary(nodes, p.lane); el.classList.toggle('attn', nodes.some(n => n.state !== 'ok')); continue; }
    if (p.role === 'service') paintService(p, el, rate);
    el.classList.toggle('down', /✗/.test(el.querySelector('[data-k=s]')?.textContent || ''));
  }
  for (const n of nodeList()) for (const sq of document.querySelectorAll(`#flow .cv-sq[data-ent="${CSS.escape(n.name)}"]`)) {
    sq.setAttribute('class', 'cv-sq ' + n.state); const t = sq.querySelector('title'); if (t) t.textContent = `${n.name}: ${n.label}`;
    const gl = sq.querySelector('.cv-glyph'); if (n.state !== 'ok' && !gl) sq.append(sv('text', { class: 'cv-glyph', x: +sq.firstChild.nextSibling.getAttribute('x') + 7, y: +sq.firstChild.nextSibling.getAttribute('y') + 11, 'text-anchor': 'middle' }, STATE_ICON[n.state] || '?')); else if (n.state === 'ok' && gl) gl.remove(); else if (gl) gl.textContent = STATE_ICON[n.state] || '?';
  }
}
function paintService(p, el, rate) {
  const snap = S.flowSnap, st = el.querySelector('[data-k=s]'), key = p.id, known = snap && key in (snap.reachable || {});
  let text;
  if (!p.present) text = '';
  else if (known) { const up = snap.reachable[key], n = snap.totals?.[key], e = snap.errors?.[key]; text = up ? (e ? `▲ up · ${e} ${e === 1 ? 'error' : 'errors'}` : `✓ up · ${rate[key] || 0} req/min`) + (n === undefined ? '' : '') : stackUp() ? '✗ not reachable' : '– offline'; }
  else if (key === 'prometheus' || key === 'grafana') { const l = (S.monLinks || []).find(x => x.from === key); text = l ? (l.targets != null ? `${l.up === l.targets ? '✓' : '▲'} ${l.up} of ${l.targets} targets up` : '✓ answers') : ''; }
  else text = key === 'slurmrestd' ? (stackUp() ? '– not probed' : '– offline') : '';
  if (st.textContent !== text) st.textContent = text;
  el.classList.toggle('flt', !!(snap && snap.errors && snap.errors[key]));
}

// ---- the layer stack: isolating one plane fades the other wires (and the parts only they touch) to 10 % ----
export function applyLayer() {
  if (!spec) return; const g = $('flow'), L = S.cvLayer || 'all', on = layerParts(spec, L);
  for (const w of spec.wires) w.el.setAttribute('data-faded', L !== 'all' && LAYER_OF[w.plane] !== L ? '1' : '0');
  g.querySelectorAll('.cv-labels [data-key]').forEach(t => { const w = spec.wires.find(x => x.key === t.getAttribute('data-key')); t.setAttribute('data-faded', w && L !== 'all' && LAYER_OF[w.plane] !== L ? '1' : '0'); });
  g.querySelectorAll('[data-part]').forEach(el => el.setAttribute('data-faded', on && !on.has(el.dataset.part) ? '1' : '0'));
  paintLayerStack();
}
export function paintLayerStack() {
  const box = $('cvstack'); if (!box) return;
  const L = S.cvLayer || 'all', count = k => spec ? spec.wires.filter(w => w.present && !w.bus && (k === 'all' || LAYER_OF[w.plane] === k)).length : 0;
  box.querySelectorAll('button[data-layer]').forEach(b => { const k = b.dataset.layer; b.setAttribute('aria-pressed', k === L); b.querySelector('.n').textContent = `${count(k)} wire${count(k) === 1 ? '' : 's'}`; });
  $('cvcap').textContent = LAYER_TEXT[L];
}
export function setLayer(k) { S.cvLayer = k; applyLayer(); announce(`${LAYERS.find(x => x[0] === k)[1]}: ${LAYER_TEXT[k]}`); }
export const circuitOn = () => !!S.circuit;
export function circuitSignature() { const f = facts(); return JSON.stringify([f.slurm, f.k8s, f.ctl, f.jump, f.tele, f.dbd, f.monitoring, f.grafana, !!f.sLogin, !!f.kLogin, Object.keys(f.services).sort()]); }

export function init() {
  const box = $('cvstack'); if (!box) return;
  box.innerHTML = LAYERS.map(([k, t]) => `<button type="button" class="cvlayer" data-layer="${k}" aria-pressed="${k === 'all'}"><span class="plate" data-plane="${k}" aria-hidden="true"></span><span class="lt"><b>${esc(t)}</b><span class="n"></span></span></button>`).join('');
  box.querySelectorAll('button[data-layer]').forEach(b => { b.onclick = () => setLayer(b.dataset.layer); });
}
