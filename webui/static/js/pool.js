// The Pool level of the infrastructure zoom (redesign step 4): the side panel and the 4 x 4 tile grid of one pool, drawn into #poolview.
// poollayout.js decides what is shown; the data is the page's cluster snapshot, so nothing here fetches.
//
// Each tile has a heartbeat wire. The BMC farm and the Kubernetes API keep no per-node request counter yet (per-path counters are
// redesign step 5), so the wire is drawn "declared": lit, nothing moving, and its words say so. It pulses once a counter backs it.
import { S } from './state.js';
import { $, esc } from './util.js';
import { sv } from './svg.js';
import { buildWire } from './wire.js';
import { STATE_ICON } from './cluster.js';
import { openPop } from './inspector.js';
import { POOLS } from './circuitlayout.js';
import { facts } from './circuit.js';
import { FILTERS, POOL_FED, STATE_WORD, filterCounts, gpuBars, pageOf, poolKpis, stateBar } from './poollayout.js';

export const poolNodes = () => (S.pool && facts().pools[S.pool]) || [];
let sig = '';

function heartbeat(n, k8s) {
  const svg = sv('svg', { class: 'pv-hb', viewBox: '0 0 200 12', 'aria-hidden': 'false', role: 'group', 'aria-label': 'Heartbeat wire' });
  const live = n.state !== 'down' && n.state !== 'unknown';
  svg.append(buildWire({ d: 'M0 6 L200 6', plane: k8s ? 'k8s' : 'management', rpm: 0, declared: live, label: `${k8s ? 'KWOK' : 'BMC farm'} to ${n.name}` }));
  return svg;
}

function tile(n, k8s) {
  const li = document.createElement('li'), bars = gpuBars(n), attn = n.state !== 'ok';
  li.className = 'pv-tile ' + n.state + (attn ? ' attn' : ''); li.dataset.node = n.name;
  const main = document.createElement('button');
  main.type = 'button'; main.className = 'pv-open'; main.setAttribute('aria-label', `${n.name}: ${n.label}. Open details`);
  main.innerHTML = `<span class="pv-name mono">${esc(n.name)}</span><span class="pv-pill ${n.state}"><b aria-hidden="true">${STATE_ICON[n.state] || '?'}</b> ${esc(n.label)}</span>`;
  main.addEventListener('click', () => openPop(main, 'node', n.name));
  const meter = document.createElement('div'); meter.className = 'pv-meter';
  if (bars) meter.innerHTML = `<span class="pv-bars" role="img" aria-label="${esc(bars.text)}">${bars.bars.map(b => `<i class="${b ? 'on' : ''}"></i>`).join('')}</span><span class="pv-mt">${esc(bars.text)}</span>`;
  else meter.innerHTML = `<span class="pv-mt dim">${n.gpus > 0 ? 'GPU use not measured' : 'CPU, memory, load and network: not measured'}</span>`;
  if (n.temp != null) meter.insertAdjacentHTML('beforeend', `<span class="pv-mt dim">${esc(n.temp)} °C</span>`);
  const link = document.createElement('button');
  link.type = 'button'; link.className = 'linkbtn pv-wirelink'; link.dataset.traffic = n.name; link.textContent = 'Wire in Traffic ↗'; link.setAttribute('aria-label', `${n.name}: open Traffic`);
  li.append(heartbeat(n, k8s), main, meter, link);
  return li;
}

export function paintPool() {
  const host = $('poolview'), nodes = poolNodes(), key = S.pool, view = host.querySelector('.pv-main');
  const page = pageOf(nodes, S.poolFilter, S.poolPage);
  S.poolPage = page.page;
  const s = JSON.stringify([key, S.poolFilter, page.page, nodes.map(n => [n.name, n.state, n.label, n.gpus_busy, n.gpu_util, n.temp])]);
  if (s === sig && view) return; sig = s;
  if (!key) { host.innerHTML = ''; return; }
  const [title, lane] = POOLS[key], k8s = lane === 'Kubernetes', kp = poolKpis(nodes), counts = filterCounts(nodes), bar = stateBar(nodes);
  const kpi = (v, l) => `<div class="pv-kpi"><b class="mono">${esc(v)}</b><span>${esc(l)}</span></div>`;
  const barText = bar.map(b => `${b.n} ${STATE_WORD[b.state].toLowerCase()}`).join(', ') || 'no nodes';
  host.innerHTML = `<aside class="pv-side" aria-label="${esc(title)} summary">
      <h3>${esc(lane)} · ${esc(title)}</h3><p class="pv-fed">${esc(POOL_FED[key])}</p>
      <div class="pv-kpis">${kpi(`${kp.inService} / ${kp.total}`, 'nodes in service')}${kpi(kp.attention, 'need attention')}${kpi(kp.avgUtil === null ? 'n/a' : kp.avgUtil + ' %', kp.avgUtil === null ? 'GPU use not measured' : 'GPU use, average')}</div>
      <div class="pv-bar" role="img" aria-label="Node states: ${esc(barText)}">${bar.map(b => `<i class="${b.state}" style="flex:${b.n}"></i>`).join('')}</div>
      <ul class="pv-legend">${bar.map(b => `<li><span class="sw ${b.state}" aria-hidden="true"></span>${b.n} ${esc(STATE_WORD[b.state].toLowerCase())}</li>`).join('')}</ul>
      <div class="pv-filters" role="group" aria-label="Show nodes">${FILTERS.map(([k, t]) => `<button type="button" class="btn small" data-filter="${k}" aria-pressed="${S.poolFilter === k}"${counts[k].enabled ? '' : ' disabled title="Not measured in this pool"'}>${esc(t)} <span class="mono">${counts[k].n}</span></button>`).join('')}</div>
    </aside><div class="pv-main"><ul class="pv-grid" aria-label="${esc(title)} nodes, page ${page.page + 1} of ${page.pages}"></ul>
      <nav class="pv-pager" aria-label="Pages"><button type="button" class="btn small" data-page="-1"${page.page ? '' : ' disabled'}>‹ Previous</button><span aria-live="polite">Page ${page.page + 1} of ${page.pages} · ${page.total} ${page.total === 1 ? 'node' : 'nodes'}</span><button type="button" class="btn small" data-page="1"${page.page < page.pages - 1 ? '' : ' disabled'}>Next ›</button></nav></div>`;
  const grid = host.querySelector('.pv-grid');
  if (!page.items.length) grid.innerHTML = `<li class="pv-empty">${nodes.length ? 'No node matches this filter.' : 'This pool has no nodes.'}</li>`;
  grid.append(...page.items.map(n => tile(n, k8s)));
}

export function init() {
  const host = $('poolview'); if (!host) return;
  host.addEventListener('click', e => {
    const f = e.target.closest('[data-filter]'), p = e.target.closest('[data-page]'), t = e.target.closest('[data-traffic]');
    if (f && !f.disabled) { S.poolFilter = f.dataset.filter; S.poolPage = 0; paintPool(); host.querySelector(`[data-filter="${S.poolFilter}"]`)?.focus(); }
    if (p && !p.disabled) { S.poolPage += Number(p.dataset.page); paintPool(); host.querySelector(`[data-page="${p.dataset.page}"]:not([disabled])`)?.focus(); }
    if (t) import('./shell.js').then(m => m.selectTab('traffic', true));
  });
}
