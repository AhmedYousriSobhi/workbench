// The infrastructure zoom (redesign step 4): three levels, Fleet, Cluster and Pool, each a full layer, and the move between them.
//
// Levels map onto what the page already has: Fleet is the overview level (curLevel() === 'overview'), Cluster is the Circuit
// view, Pool is S.pool. Moving is a transient: the outgoing layer scales (2.6x going in, 0.35x going out) and fades, then is
// removed from layout, so it can never stay visible or clickable; the incoming layer grows from the card or rack that was
// clicked. About 0.7 s. Under reduced motion the layers simply swap.
import { S } from './state.js';
import { $, announce } from './util.js';
import { reduced } from './flowstate.js';
import { clusterList, clusterTitle, curLevel, enterCluster, setLevel } from './overview.js';
import { paintFleet } from './fleet.js';
import { paintPool, poolNodes } from './pool.js';
import { facts } from './circuit.js';
import { POOLS } from './circuitlayout.js';
import { paintCrumb } from './focus.js';
import { selectTab } from './shell.js';

export const ZOOM_LEVELS = ['fleet', 'cluster', 'pool', 'traffic'], LEVEL_WORD = { fleet: 'Fleet', cluster: 'Cluster', pool: 'Pool', traffic: 'Traffic' };
export const MS = 700, EASE = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
const scroller = () => document.querySelector('#trafficpanel > .flowscroll');
const layerEl = lv => lv === 'fleet' ? $('fleetview') : lv === 'pool' ? $('poolview') : lv === 'traffic' ? $('tab-traffic') : scroller();
const onTraffic = () => !$('tab-traffic').hidden, onConn = () => !$('tab-conn').hidden;

// The level of the infrastructure itself, and the level the page is at: Traffic is the deepest stop, a tab of its own that the rail reaches.
export const infraLevel = () => !S.circuit ? null : curLevel() === 'overview' ? 'fleet' : S.pool ? 'pool' : 'cluster';
export const levelNow = () => !S.circuit ? null : onTraffic() ? 'traffic' : infraLevel();
const firstPool = () => Object.keys(POOLS).find(k => facts().pools[k].length);

function paintLevel(lv) { if (lv === 'fleet') paintFleet(); else if (lv === 'pool') paintPool(); }
export const refreshZoom = () => { if (!S.zoomHold) paintLevel(infraLevel()); };

function paintRail(lv) {
  const rail = $('zoomrail'); rail.hidden = !lv || onConn();
  if (rail.hidden) return;
  rail.querySelectorAll('[data-level]').forEach(b => {
    const on = b.dataset.level === lv; b.setAttribute('aria-pressed', on); if (on) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
    if (b.dataset.level === 'pool') b.disabled = !firstPool() && lv !== 'pool';
  });
  $('zr-out').disabled = lv === 'fleet'; $('zr-in').disabled = lv === 'traffic';
}

// Show exactly the layer of the current level, with no transition: used on every data change, on a rebuild and when a transition ends.
export function syncZoom() {
  if (S.zoomHold) return;
  const lv = infraLevel();
  for (const k of ['fleet', 'cluster', 'pool']) { const el = layerEl(k); if (!el) continue; const on = lv === null ? k === 'cluster' : k === lv; el.hidden = !on; el.inert = !on; }
  const cluster = lv === 'cluster'; $('cvstack').hidden = !cluster; $('cvcap').hidden = !cluster;
  $('subseg').hidden = !!S.circuit || $('tab-nodes').hidden;  // the zoom replaces the All / Topology / Nodes switch
  $('zback').hidden = !(S.circuit && onTraffic()); $('zback-where').textContent = `Traffic of ${clusterTitle()}`;
  paintLevel(lv); paintRail(levelNow()); paintCrumb();
}

const origin = (el, into) => {
  if (!el || !el.getBoundingClientRect) return '50% 50%';
  const a = el.getBoundingClientRect(), b = into.getBoundingClientRect(); return `${Math.round(a.left + a.width / 2 - b.left)}px ${Math.round(a.top + a.height / 2 - b.top)}px`;
};
const ALL = () => [...['fleet', 'cluster', 'pool', 'traffic'].map(layerEl), $('tab-nodes')];
function settle() { for (const el of ALL()) { if (!el) continue; el.getAnimations?.().forEach(a => a.cancel()); el.classList.remove('zout'); for (const p of ['position', 'left', 'top', 'width', 'transform-origin']) el.style.removeProperty(p); } }
const moves = () => !reduced.matches && !!document.body.animate;

// Inside the infrastructure: Fleet, Cluster and Pool are three layers of one panel; the outgoing one lies over the incoming one while it leaves.
function transition(from, to, from_el) {
  const inEl = layerEl(to), outEl = layerEl(from), tok = S.zoomTok = (S.zoomTok || 0) + 1, dirIn = ZOOM_LEVELS.indexOf(to) > ZOOM_LEVELS.indexOf(from);
  if (!moves() || !outEl || !inEl) { S.zoomHold = false; syncZoom(); return; }
  paintLevel(to);
  const o = origin(from_el, inEl);
  const x = outEl.offsetLeft, y = outEl.offsetTop, w = outEl.offsetWidth;
  inEl.hidden = false; inEl.inert = false; outEl.inert = true;
  outEl.classList.add('zout'); Object.assign(outEl.style, { left: x + 'px', top: y + 'px', width: w + 'px' });
  inEl.style.transformOrigin = o; outEl.style.transformOrigin = o;
  const opt = { duration: MS, easing: EASE, fill: 'both' };
  const a = outEl.animate([{ transform: 'scale(1)', opacity: 1 }, { transform: `scale(${dirIn ? 2.6 : 0.35})`, opacity: 0 }], opt);
  inEl.animate([{ transform: `scale(${dirIn ? 0.4 : 1.8})`, opacity: 0 }, { transform: 'scale(1)', opacity: 1 }], opt);
  const done = () => { if (S.zoomTok !== tok) return; settle(); S.zoomHold = false; syncZoom(); };
  a.finished.then(done, () => {}); setTimeout(done, MS + 250);  // a hidden tab may not run the animation: the end state still arrives
}

// The state change of a move inside the infrastructure (no drawing).
function setInfra(level, o) {
  const from = infraLevel();
  if (level === 'fleet') { S.pool = null; setLevel('overview', false); return; }
  if (from === 'fleet') enterCluster(o.cluster || clusterList()[0]);
  const pool = level === 'pool' ? o.pool || firstPool() : null;
  S.pool = pool; if (pool) { S.poolFilter = 'all'; S.poolPage = 0; }
}

// Between the infrastructure and Traffic the two tab panels trade places: the leaving one zooms away over half the time, the arriving one grows in.
function viaTab(level, o) {
  const toTraffic = level === 'traffic', outEl = toTraffic ? $('tab-nodes') : $('tab-traffic'), tok = S.zoomTok = (S.zoomTok || 0) + 1;
  const swap = () => { if (toTraffic) selectTab('traffic', false); else { setInfra(level, o); selectTab('nodes', false); } syncZoom(); };
  if (!moves()) { swap(); return; }
  const half = MS / 2, opt = { duration: half, easing: EASE, fill: 'forwards' };
  outEl.style.transformOrigin = origin(o.origin, outEl);
  const out = outEl.animate([{ transform: 'scale(1)', opacity: 1 }, { transform: `scale(${toTraffic ? 2.6 : 0.35})`, opacity: 0 }], opt);
  let did = false;
  const arrive = () => {
    if (did || S.zoomTok !== tok) return; did = true; swap(); out.cancel(); outEl.style.removeProperty('transform-origin');
    const inEl = toTraffic ? $('tab-traffic') : $('tab-nodes'); inEl.style.transformOrigin = '50% 30%';
    inEl.animate([{ transform: `scale(${toTraffic ? 0.4 : 1.8})`, opacity: 0 }, { transform: 'scale(1)', opacity: 1 }], { duration: half, easing: EASE }).finished.then(() => inEl.style.removeProperty('transform-origin'), () => {});
  };
  out.finished.then(arrive, () => {}); setTimeout(arrive, half + 200);
}

// Move to a level. `o.origin` is the element the new layer grows from (a card or a rack); `o.pool` picks the pool, `o.cluster` the cluster.
export function zoomTo(level, o = {}) {
  if (!S.circuit) return;
  settle(); S.zoomHold = false; syncZoom();  // a move still running ends where it was going
  const from = levelNow();
  if (level === from && !(level === 'pool' && o.pool && o.pool !== S.pool)) return;
  if (level === 'pool' && !(o.pool || firstPool())) return;  // nothing to open: a cluster with no nodes has no pool
  if (from === 'traffic' || level === 'traffic') viaTab(level, o);
  else {
    S.zoomHold = true;
    try { setInfra(level, o); } catch (e) { S.zoomHold = false; throw e; }
    transition(from, infraLevel(), o.origin);
  }
  const to = level;
  announce(to === 'pool' ? `${LEVEL_WORD[to]} level: ${POOLS[S.pool || o.pool || firstPool()].join(' · ')}` : `${LEVEL_WORD[to]} level`);
}

export function zoomStep(dir, o = {}) {
  const lv = levelNow(); if (!lv) return;
  const i = ZOOM_LEVELS.indexOf(lv) + dir;
  if (i >= 0 && i < ZOOM_LEVELS.length) zoomTo(ZOOM_LEVELS[i], { origin: $(dir > 0 ? 'zr-in' : 'zr-out'), ...o });
}

// Ctrl + scroll (or a pinch on a trackpad) zooms like a map: in on what is under the pointer, out from anywhere. One level per gesture.
function onWheel(e) {
  if (!S.circuit || !(e.ctrlKey || e.metaKey) || onConn() || !e.target.closest?.('#main')) return;
  e.preventDefault();  // not the browser's page zoom
  const now = performance.now();
  if (now - (S.zoomWheelAt || 0) > 350) S.zoomAcc = 0;
  S.zoomWheelAt = now;
  if (now < (S.zoomLock || 0)) return;
  S.zoomAcc = (S.zoomAcc || 0) + e.deltaY;
  if (Math.abs(S.zoomAcc) < 40) return;
  const dir = S.zoomAcc < 0 ? 1 : -1; S.zoomAcc = 0; S.zoomLock = now + MS + 150;
  const at = e.target.closest('.fl-card, .cv-rack'), o = { origin: e.target };
  if (dir > 0 && at?.classList.contains('fl-card')) o.cluster = at.dataset.cluster;
  if (dir > 0 && at?.classList.contains('cv-rack') && at.dataset.pool && facts().pools[at.dataset.pool].length) o.pool = at.dataset.pool;
  zoomStep(dir, o);
}

export function init() {
  const rail = $('zoomrail'); if (!rail) return;
  rail.querySelectorAll('[data-level]').forEach(b => { b.onclick = () => zoomTo(b.dataset.level, { origin: b }); });
  $('zr-out').onclick = () => zoomStep(-1); $('zr-in').onclick = () => zoomStep(1);
  $('tr-back').onclick = () => zoomTo(infraLevel());
  document.addEventListener('wheel', onWheel, { passive: false });
  document.addEventListener('keydown', e => {
    if (!S.circuit || e.ctrlKey || e.metaKey || e.altKey || e.target.closest?.('input, textarea, select, [contenteditable]') || document.querySelector('dialog[open]:not(.drawer)')) return;
    if (e.key === '+' || e.key === '=') zoomStep(1); else if (e.key === '-' || e.key === '_') zoomStep(-1);
    else if (e.key === 'Escape' && levelNow() === 'pool' && !S.openNode && $('ckey').hidden && $('pop').hidden) zoomTo('cluster');
    else if (e.key === 'Escape' && levelNow() === 'traffic') zoomTo(infraLevel());
  });
}
