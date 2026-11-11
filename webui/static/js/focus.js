import { S } from './state.js';
import { accExtra, svcList } from './flowstate.js';
import { $, announce, esc } from './util.js';
import { clusterNow, hidePop, nodeList, paintInspector } from './inspector.js';
import { loginList } from './geometry.js';
import { clusterTitle, curLevel } from './overview.js';

export const ENT_NAME = id => ({ clients: 'Clients', faults: 'Faults', nodes: 'Cluster nodes', teleport: 'Teleport proxy', jump: 'Jump host', controller: 'Controller' })[id] || (svcList().find(x => x[0] === id) || [])[1] || id;
export function tagEntities() {  // each clickable or framed part of the drawing gets the id of its entity in the declared graph
  const g = $('flow'), tag = (el, id) => { if (el) el.setAttribute('data-ent', id); };
  for (const [k] of svcList()) tag(document.getElementById('fb-' + k)?.parentNode, k);
  for (const l of loginList()) tag(document.getElementById('lg-' + l.name), l.name);
  for (const [k] of accExtra()) tag(document.getElementById('ac-' + k), k);
  for (const n of nodeList()) tag(document.getElementById('nd-' + n.name), n.name);
  g.querySelectorAll('g.hit').forEach(h => { if (!h.hasAttribute('data-ent') && h.querySelector('.chipbg')) tag(h, 'clients'); });
  const fr = g.querySelectorAll(':scope > .frame'); if (fr.length >= 5) { tag(fr[0], 'clients'); tag(fr[1], 'faults'); tag(fr[4], 'nodes'); }
}
export function focusSet(id) {  // the entity, its neighbours over declared edges, and those edges; a node stands for the group it belongs to
  const gr = clusterNow().graph; if (!gr) return null;
  const ent = gr.entities.find(e => e.id === id), via = ent && ent.group ? ent.group : id, ents = new Set([id, via]), edges = new Set();
  for (const e of gr.edges) if (e.from === via || e.to === via) { edges.add(e.id); ents.add(e.from); ents.add(e.to); }
  return { ents, edges, via, edgeList: gr.edges.filter(e => edges.has(e.id)), group: gr.entities.filter(e => e.group && ents.has(e.group)).map(e => e.id) };
}
export function applyFocus() {
  const g = $('flow'), fs = S.focusId ? focusSet(S.focusId) : null, gr = clusterNow().graph;
  if (S.focusId && !fs) S.focusId = null;
  const keep = el => !!el.querySelector('.blk.down, .nd.fault, .nd.warn, .fring.on');  // faulted parts stay visible while the rest is dimmed
  g.querySelectorAll('[data-ent]').forEach(el => {
    const id = el.getAttribute('data-ent'), ent = gr && gr.entities.find(e => e.id === id), inSet = fs && (fs.ents.has(id) || (ent && ent.group && fs.ents.has(ent.group)) || id === S.focusId);
    if (fs && !inSet && !keep(el)) el.setAttribute('data-dim', '1'); else el.removeAttribute('data-dim');
    if (fs && id === S.focusId) el.setAttribute('data-focus', '1'); else el.removeAttribute('data-focus');
  });
  g.querySelectorAll('path.lane[data-edge]').forEach(l => { if (fs && !fs.edges.has(l.getAttribute('data-edge'))) l.setAttribute('data-dim', '1'); else l.removeAttribute('data-dim'); });
  g.querySelectorAll('.elabs, .lanehalo').forEach(el => { if (fs) el.setAttribute('data-dim', '1'); else el.removeAttribute('data-dim'); });
  paintFocusPane(fs); paintCrumb();
}
export function paintFocusPane(fs) {
  const pane = $('focuspane'); pane.hidden = !fs; paintInspector(); if (!fs) { pane.innerHTML = ''; return; }
  const rows = fs.edgeList.map(e => { const out = e.from === fs.via, other = out ? e.to : e.from;
    return `<tr><td>${out ? 'to' : 'from'} <b>${esc(ENT_NAME(other))}</b></td><td>${esc(e.plane)}</td><td>${esc(e.kind)}</td><td>${e.measured ? 'measured (a counter backs it)' : 'declared (how it is wired, not traffic seen)'}</td></tr>`; }).join('');
  pane.innerHTML = `<b>${esc(ENT_NAME(S.focusId))}: what it connects to</b><p class="small dim">Everything else is dimmed; faulted parts stay visible. Esc or the breadcrumb returns.</p>
    <table class="focustab"><thead><tr><th>Connection</th><th>Plane</th><th>Kind</th><th>Basis</th></tr></thead><tbody>${rows || '<tr><td colspan="4">No declared connections.</td></tr>'}</tbody></table>`;
}
export function focusOn(id) { S.focusId = id; if (id) hidePop(false); applyFocus(); announce(id ? `Focused on ${ENT_NAME(id)} and its neighbours` : 'Focus cleared'); }
export function paintCrumb() {
  const ov = curLevel() === 'overview', show = !ov && (S.dived || S.focusId);
  $('crumb').hidden = !show; $('crumb-all').hidden = !S.dived; $('crumb-sep1').hidden = !S.dived;
  $('crumb-name').textContent = clusterTitle(); $('crumb-sep2').hidden = !S.focusId; $('crumb-focus').textContent = S.focusId ? ENT_NAME(S.focusId) : '';
}

export function init() {
  $('flow').addEventListener('dblclick', e => { const el = e.target.closest('[data-ent]'); if (curLevel() === 'overview') return; if (el) focusOn(el.getAttribute('data-ent') === S.focusId ? null : el.getAttribute('data-ent')); else if (S.focusId) focusOn(null); });
  $('flow').addEventListener('keydown', e => { if ((e.key === 'f' || e.key === 'F') && !e.ctrlKey && !e.metaKey) { const el = e.target.closest && e.target.closest('[data-ent]'); if (el) { e.preventDefault(); focusOn(el.getAttribute('data-ent') === S.focusId ? null : el.getAttribute('data-ent')); } } });
  $('crumb-name').onclick = () => { if (S.focusId) focusOn(null); };

}
