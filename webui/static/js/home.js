import { S } from './state.js';
import { $, esc, paintDyn } from './util.js';
import { REAL_SERVICES } from './flowstate.js';
import { LEVELS } from './governor.js';
import { attentionItems, homeNodeCard, homeServices, homeSummary } from './homemodel.js';
import { selectTab } from './shell.js';
import { showNode } from './node.js';
import { scnOpen } from './scenarios.js';

// Overview (screen 1): the first view answers the four jobs: is it up, break something, what did my client call, how do I connect.
// It repaints from the data the page already holds (snapshot S.lastCl, governor S.lastGov); it fetches nothing of its own.
export function paintHome() {
  if (!$('tab-home') || $('tab-home').hidden) return;  // a hidden workspace costs nothing (W-11)
  const cl = S.lastCl, items = attentionItems(S.lastGov, cl, LEVELS), bad = items.some(i => i.kind === 'bad');
  $('home-sub').textContent = homeSummary(cl);
  $('home-attn-h').firstChild.textContent = items.length ? `${bad ? '✗' : '▲'} Needs attention (${items.length})` : '✓ All clear';
  $('home-attn').dataset.state = items.length ? (bad ? 'bad' : 'warn') : 'ok';
  $('home-attn-list').innerHTML = items.length ? items.map(i => `<li><span class="ic ${i.kind}" aria-hidden="true">${i.icon}</span><span>${esc(i.text)}</span>${i.node ? `<button type="button" class="linkbtn" data-open="${esc(i.node)}">Open ${esc(i.node)}</button>` : ''}</li>`).join('')
    : '<li><span class="ic good" aria-hidden="true">✓</span><span>Nothing needs you right now.</span></li>';
  const nodes = cl && cl.nodes ? cl.nodes.map(homeNodeCard) : [];
  $('home-nodes').innerHTML = nodes.length ? nodes.map(c => `<li><button type="button" class="hn ${c.state}" data-open="${esc(c.name)}" aria-label="${esc(c.name)}: ${esc(c.label)}. Open it in Topology">
    <span class="hn-top"><span class="mono">${esc(c.name)}</span><span class="dim small">${esc(c.kind)}</span><span class="pill ${c.state}"><b aria-hidden="true">${c.icon}</b> ${esc(c.label)}</span></span>
    ${c.load === null ? '' : `<span class="hn-bar" role="img" aria-label="GPU use ${c.load} percent"><i data-w="${c.load}"></i></span>`}
    <span class="dim small">${esc(c.detail)}</span></button></li>`).join('') : '<li class="dim small">No nodes yet: the stack is not running or the first snapshot is still on its way.</li>';
  paintDyn($('home-nodes'));
  const svcs = cl && cl.services ? homeServices(cl.services, REAL_SERVICES) : [];
  $('home-svcs').innerHTML = svcs.map(s => `<li class="hs ${s.up ? 'ok' : 'fault'}"><b class="ic ${s.up ? 'good' : 'bad'}" aria-hidden="true">${s.icon}</b><span>${esc(s.name)}</span><span class="dim small">${s.text}</span><span class="tag">${s.real ? 'real' : 'simulated'}</span></li>`).join('');
}

export function openNodeInTopology(name) { selectTab('nodes'); showNode(name); }

export function init() {
  $('home-run').onclick = () => scnOpen(true);
  $('home-break').onclick = () => selectTab('nodes');
  $('home-connect').onclick = () => $('expbtn').click();
  $('tab-home').addEventListener('click', e => { const b = e.target.closest('[data-open]'); if (b) openNodeInTopology(b.dataset.open); });
}
