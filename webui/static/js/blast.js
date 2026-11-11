import { S } from './state.js';
import { clusterNow, paintInspector } from './inspector.js';
import { ENT_NAME } from './focus.js';
import { $, announce, esc } from './util.js';

export function blastFor(ev) {  // the declared entry for an injection event: "<action> on <node>" from the buttons, else the service it hit
  const bl = clusterNow().blast; if (!bl) return null;
  const act = String(ev.label || '').split(' on ')[0].trim(), f = bl.faults[act];
  if (f) return { services: f.services, nodes: ev.nodes || [], caption: f.caption, basis: bl.basis, title: `${act}${(ev.nodes || []).length ? ' on ' + ev.nodes.join(', ') : ''}` };
  const sc = bl.services[ev.to]; return sc ? { services: [ev.to], nodes: ev.nodes || [], caption: sc, basis: bl.basis, title: `fault on ${ENT_NAME(ev.to)}` } : null;
}
export function applyBlast() {
  const g = $('flow'), b = S.blastNow;
  g.querySelectorAll('[data-blast]').forEach(el => el.removeAttribute('data-blast'));
  if (b) {
    for (const k of b.services) g.querySelectorAll(`[data-ent="${k}"]`).forEach(el => el.setAttribute('data-blast', '1'));
    for (const n of b.nodes) g.querySelectorAll(`[data-ent="${n}"]`).forEach(el => el.setAttribute('data-blast', '1'));
    for (const k of b.services) g.querySelectorAll(`path.lane[data-edge="clients>${k}:api"], g.wire[data-edge="clients>${k}:api"]`).forEach(el => el.setAttribute('data-blast', '1'));
  }
  const pane = $('blastpane'); pane.hidden = !b; paintInspector();
  pane.innerHTML = b ? `<b>Blast radius: ${esc(b.title)}</b><p>${esc(b.caption)}</p><p class="small dim">Dashed parts are affected: ${b.services.map(k => esc(ENT_NAME(k))).join(', ')}${b.nodes.length ? '; node ' + b.nodes.map(esc).join(', ') : ''}. Basis: ${esc(b.basis)}.</p><button class="btn small" id="blast-x">Dismiss</button>` : '';
  if (b) $('blast-x').onclick = () => setBlast(null);
}
export function setBlast(b) { S.blastNow = b; clearTimeout(S.blastTimer); if (b) S.blastTimer = setTimeout(() => setBlast(null), 45000); applyBlast(); if (b) announce(`Fault: ${b.title}. ${b.caption} (${b.basis})`); }
