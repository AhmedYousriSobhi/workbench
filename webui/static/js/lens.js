import { S } from './state.js';
import { clusterNow, paintInspector } from './inspector.js';
import { $, announce, esc } from './util.js';
import { stackUp } from './geometry.js';
import { ENT_NAME } from './focus.js';

export const PLANE_WORD = { access: 'how users get in', management: 'BMC and BCM', telemetry: 'GPU metrics', tickets: 'Jira', faults: 'fault injection' };
export function lensSet() {  // entities and edges of the lens plane; the client's eye is every client edge to a service
  const gr = clusterNow().graph; if (!gr || S.lens === 'all') return null;
  const edges = gr.edges.filter(e => S.lens === 'client' ? e.from === 'clients' && e.kind === 'api' : e.plane === S.lens), ents = new Set();
  for (const e of edges) { ents.add(e.from); ents.add(e.to); }
  return { edges: new Set(edges.map(e => e.id)), ents };
}
export function applyLens() {
  const g = $('flow'), ls = lensSet(), gr = clusterNow().graph;
  g.querySelectorAll('[data-ent]').forEach(el => {
    const id = el.getAttribute('data-ent'), ent = gr && gr.entities.find(e => e.id === id), on = !ls || ls.ents.has(id) || (ent && ent.group && ls.ents.has(ent.group));
    if (on) el.removeAttribute('data-ldim'); else el.setAttribute('data-ldim', '1');
  });
  g.querySelectorAll('path.lane[data-edge]').forEach(l => { if (!ls || ls.edges.has(l.getAttribute('data-edge'))) l.removeAttribute('data-ldim'); else l.setAttribute('data-ldim', '1'); });
  g.querySelectorAll('.elabs').forEach(el => { if (ls) el.setAttribute('data-ldim', '1'); else el.removeAttribute('data-ldim'); });
  $('trafficpanel').dataset.lens = S.lens; paintEye();
}
export function paintEye() {  // what the monitoring client called, from the per-mock counters; "not measured" where nothing counts
  const pane = $('eyepane'), on = S.lens === 'client'; pane.hidden = !on; paintInspector(); if (!on) { pane.innerHTML = ''; return; }
  const gr = clusterNow().graph, snap = S.flowSnap;
  if (!gr || !stackUp()) { pane.innerHTML = `<b>Client's eye</b><p class="small dim">The simulation stack is not running, so there are no calls to show. Start it with <code>./scripts/benchctl start</code>.</p>`; return; }
  const rate = {}, cut = Date.now() - 60000; S.recent.filter(r => r.t > cut).forEach(r => { rate[r.to] = (rate[r.to] || 0) + r.n; });
  const rows = gr.edges.filter(e => e.from === 'clients' && e.kind === 'api').map(e => { const k = e.to, tot = snap && snap.totals ? snap.totals[k] : undefined, er = snap && snap.errors ? snap.errors[k] : undefined;
    const verdict = !e.measured ? 'not measured (no request counter)' : !snap ? 'waiting for the first reading' : !snap.reachable[k] ? 'not reachable' : tot ? 'called' : 'not called yet';
    return `<tr><td><b>${esc(ENT_NAME(k))}</b><br><span class="dim small">${esc(e.plane)}: ${esc(PLANE_WORD[e.plane] || '')}</span></td><td>${e.measured && snap ? (tot || 0).toLocaleString() : 'n/a'}</td><td>${e.measured && er !== undefined ? er : e.measured ? 'n/a' : 'n/a'}</td><td>${e.measured ? (rate[k] || 0) : 'n/a'}</td><td>${esc(verdict)}</td></tr>`; }).join('');
  pane.innerHTML = `<b>Client's eye: what was called</b><p class="small dim">Every service a client should call, with the calls the mocks counted since the page opened (errors only where the mock counts them). Basis: counters, not a trace of which endpoint was used.</p>
    <table class="focustab"><thead><tr><th>Service</th><th>Calls</th><th>Errors</th><th>Per min</th><th>Verdict</th></tr></thead><tbody>${rows}</tbody></table>`;
}

export function init() {
  $('lens').onchange = () => { S.lens = $('lens').value; applyLens(); announce(S.lens === 'all' ? 'Lens cleared' : `Lens: ${$('lens').selectedOptions[0].textContent}`); };

}
