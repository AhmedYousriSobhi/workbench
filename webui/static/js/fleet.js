// The Fleet level of the infrastructure zoom (redesign step 4): one card per cluster, drawn into #fleetview. fleetlayout.js decides
// what a card says. Only the running stack is measured, so the request rate and the accounting probe belong to the one cluster
// the page can see; a card for any other cluster says "not probed" or "not measured" rather than showing a number.
import { S } from './state.js';
import { $, esc } from './util.js';
import { buildWire } from './wire.js';
import { STATE_ICON } from './cluster.js';
import { recentRate } from './circuit.js';
import { fleetCards } from './fleetlayout.js';
import { zoomTo } from './zoom.js';

let sig = '';
const snapshot = () => { try { return JSON.parse(S.lastCluster); } catch (e) { return null; } };

function lane(l) {
  const sq = l.squares.map(q => `<i class="fl-sq ${q.state}" title="${esc(q.names.length === 1 ? q.names[0] + ': ' + q.label : q.names[0] + ' to ' + q.names.at(-1) + ': ' + q.label)}">${q.state === 'ok' ? '' : esc(STATE_ICON[q.state] || '?')}</i>`).join('');
  return `<div class="fl-lane${l.present ? '' : ' absent'}"><span class="fl-lt">${esc(l.title)}</span>${l.present ? `<span class="fl-sqs">${sq}</span><span class="fl-lc mono">${l.count}${l.per > 1 ? ' · one square = ' + l.per : ''}</span>` : '<span class="fl-lc">not in this cluster</span>'}</div>`;
}

export function paintFleet() {
  const host = $('fleetview'), snap = snapshot(); if (!snap) return;
  const rate = Object.values(recentRate()).reduce((a, b) => a + b, 0), cards = fleetCards(snap, { known: true, dbd: !!S.dbdLink, rpm: S.flowSnap ? rate : null });
  const s = JSON.stringify(cards); if (s === sig && host.firstChild) return; sig = s;
  host.innerHTML = cards.map((c, i) => `<article class="fl-card ${c.health.state}" data-cluster="${esc(c.name)}">
      <button type="button" class="fl-head" aria-label="Open the ${esc(c.name)} cluster"><span class="fl-name">${esc(c.name)}</span><span class="pv-pill ${c.health.state}"><b aria-hidden="true">${STATE_ICON[c.health.state] || '?'}</b> ${esc(c.health.text)}</span></button>
      <ul class="fl-badges">${c.badges.map(b => `<li class="${b.on ? 'on' : ''}${b.dashed ? ' dashed' : ''}">${b.on ? '✓ ' : ''}${esc(b.label)}</li>`).join('')}</ul>
      <div class="fl-kpis"><div class="pv-kpi"><b class="mono">${c.kpis.inService} / ${c.kpis.total}</b><span>nodes in service</span></div><div class="pv-kpi"><b class="mono">${c.kpis.rpm === null ? 'n/a' : c.kpis.rpm}</b><span>${c.kpis.rpm === null ? 'requests not measured' : 'requests a minute'}</span></div><div class="pv-kpi"><b class="mono">${c.kpis.attention}</b><span>need attention</span></div></div>
      ${c.lanes.map(lane).join('')}
      <div class="fl-foot"><svg viewBox="0 0 240 12" class="fl-wire" data-i="${i}" role="group" aria-label="Monitoring client to ${esc(c.name)}"></svg><span>Only the monitoring client connects to a cluster</span></div></article>`).join('');
  host.querySelectorAll('.fl-wire').forEach(svg => {
    const c = cards[Number(svg.dataset.i)];
    svg.append(buildWire({ d: 'M0 6 L240 6', plane: 'access', rpm: c.kpis.rpm || 0, declared: c.kpis.rpm === null, label: `Monitoring client to ${c.name}` }));
  });
}

export function init() {
  const host = $('fleetview'); if (!host) return;
  host.addEventListener('click', e => {
    const card = e.target.closest('.fl-card'); if (card) zoomTo('cluster', { cluster: card.dataset.cluster, origin: card });
  });
}
