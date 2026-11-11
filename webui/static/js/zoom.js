// The infrastructure zoom (redesign step 4): three levels, Fleet, Cluster and Pool, each a full layer, and the move between them.
//
// Levels map onto what the page already has: Fleet is the overview level (curLevel() === 'overview'), Cluster is the Circuit
// view, Pool is S.pool. Moving is a transient: the outgoing layer scales (2.6x going in, 0.35x going out) and fades, then is
// removed from layout, so it can never stay visible or clickable; the incoming layer grows from the card or rack that was
// clicked. About 0.7 s. Under reduced motion the layers simply swap.
import { S } from './state.js';
import { $, announce } from './util.js';
import { reduced } from './flowstate.js';
import { clusterList, curLevel, enterCluster, setLevel } from './overview.js';
import { paintFleet } from './fleet.js';
import { paintPool, poolNodes } from './pool.js';
import { facts } from './circuit.js';
import { POOLS } from './circuitlayout.js';
import { paintCrumb } from './focus.js';
import { selectTab } from './shell.js';

export const ZOOM_LEVELS = ['fleet', 'cluster', 'pool'], LEVEL_WORD = { fleet: 'Fleet', cluster: 'Cluster', pool: 'Pool' };
export const MS = 700, EASE = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
const scroller = () => document.querySelector('#trafficpanel > .flowscroll');
const layerEl = lv => lv === 'fleet' ? $('fleetview') : lv === 'pool' ? $('poolview') : scroller();

export const levelNow = () => !S.circuit ? null : curLevel() === 'overview' ? 'fleet' : S.pool ? 'pool' : 'cluster';
const firstPool = () => Object.keys(POOLS).find(k => facts().pools[k].length);

function paintLevel(lv) { if (lv === 'fleet') paintFleet(); else if (lv === 'pool') paintPool(); }
export const refreshZoom = () => { if (!S.zoomHold) paintLevel(levelNow()); };

function paintRail(lv) {
  const rail = $('zoomrail'); rail.hidden = !lv;
  if (!lv) return;
  rail.querySelectorAll('[data-level]').forEach(b => {
    const on = b.dataset.level === lv; b.setAttribute('aria-pressed', on); if (on) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
    if (b.dataset.level === 'pool') b.disabled = !firstPool() && lv !== 'pool';
  });
  $('zr-out').disabled = lv === 'fleet';
  $('zr-in').title = lv === 'pool' ? 'Open Traffic' : 'Zoom in';
}

// Show exactly the layer of the current level, with no transition: used on every data change, on a rebuild and when a transition ends.
export function syncZoom() {
  if (S.zoomHold) return;
  const lv = levelNow();
  for (const k of ZOOM_LEVELS) { const el = layerEl(k); if (!el) continue; const on = lv === null ? k === 'cluster' : k === lv; el.hidden = !on; el.inert = !on; }
  const cluster = lv === 'cluster'; $('cvstack').hidden = !cluster; $('cvcap').hidden = !cluster;
  paintLevel(lv); paintRail(lv); paintCrumb();
}

const origin = (el, into) => {
  if (!el || !el.getBoundingClientRect) return '50% 50%';
  const a = el.getBoundingClientRect(), b = into.getBoundingClientRect(); return `${Math.round(a.left + a.width / 2 - b.left)}px ${Math.round(a.top + a.height / 2 - b.top)}px`;
};
function settle() { for (const lv of ZOOM_LEVELS) { const el = layerEl(lv); if (!el) continue; el.getAnimations?.().forEach(a => a.cancel()); el.classList.remove('zout'); for (const p of ['position', 'left', 'top', 'width', 'transform-origin']) el.style.removeProperty(p); } }

function transition(from, to, from_el) {
  const inEl = layerEl(to), outEl = layerEl(from), tok = S.zoomTok = (S.zoomTok || 0) + 1, dirIn = ZOOM_LEVELS.indexOf(to) > ZOOM_LEVELS.indexOf(from);
  if (reduced.matches || !outEl || !inEl || !outEl.animate) { S.zoomHold = false; syncZoom(); return; }
  paintLevel(to);
  const o = origin(from_el, inEl);
  const x = outEl.offsetLeft, y = outEl.offsetTop, w = outEl.offsetWidth;
  inEl.hidden = false; inEl.inert = false; outEl.inert = true;
  outEl.classList.add('zout'); Object.assign(outEl.style, { left: x + 'px', top: y + 'px', width: w + 'px' });  // the outgoing layer lies over the incoming one while it leaves
  inEl.style.transformOrigin = o; outEl.style.transformOrigin = o;
  const opt = { duration: MS, easing: EASE, fill: 'both' };
  const a = outEl.animate([{ transform: 'scale(1)', opacity: 1 }, { transform: `scale(${dirIn ? 2.6 : 0.35})`, opacity: 0 }], opt);
  inEl.animate([{ transform: `scale(${dirIn ? 0.4 : 1.8})`, opacity: 0 }, { transform: 'scale(1)', opacity: 1 }], opt);
  const done = () => { if (S.zoomTok !== tok) return; settle(); S.zoomHold = false; syncZoom(); };
  a.finished.then(done, () => {}); setTimeout(done, MS + 250);  // a hidden tab may not run the animation: the end state still arrives
}

// Move to a level. `o.origin` is the element the new layer grows from (a card or a rack); `o.pool` picks the pool, `o.cluster` the cluster.
export function zoomTo(level, o = {}) {
  if (!S.circuit) return;
  settle(); S.zoomHold = false; syncZoom();  // a move still running ends where it was going
  const from = levelNow();
  if (level === from && !(level === 'pool' && o.pool && o.pool !== S.pool)) return;
  const pool = level === 'pool' ? o.pool || firstPool() : null;
  if (level === 'pool' && !pool) return;  // nothing to open: a cluster with no nodes has no pool
  S.zoomHold = true;
  try {
    if (level === 'fleet') { S.pool = null; setLevel('overview', false); }
    else {
      if (from === 'fleet') enterCluster(o.cluster || clusterList()[0]);
      S.pool = pool; if (pool) { S.poolFilter = 'all'; S.poolPage = 0; }
    }
  } catch (e) { S.zoomHold = false; throw e; }
  const to = levelNow();
  transition(from, to, o.origin);
  announce(to === 'pool' ? `${LEVEL_WORD[to]} level: ${POOLS[S.pool].join(' · ')}, ${poolNodes().length} nodes` : `${LEVEL_WORD[to]} level`);
}

export function zoomStep(dir) {
  const lv = levelNow(); if (!lv) return;
  const i = ZOOM_LEVELS.indexOf(lv) + dir;
  if (i >= ZOOM_LEVELS.length) { selectTab('traffic', true); return; }
  if (i >= 0) zoomTo(ZOOM_LEVELS[i], { origin: $(dir > 0 ? 'zr-in' : 'zr-out') });
}

export function init() {
  const rail = $('zoomrail'); if (!rail) return;
  rail.querySelectorAll('[data-level]').forEach(b => { b.onclick = () => zoomTo(b.dataset.level, { origin: b }); });
  $('zr-out').onclick = () => zoomStep(-1); $('zr-in').onclick = () => zoomStep(1); $('zr-traffic').onclick = () => selectTab('traffic', true);
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || levelNow() !== 'pool' || S.openNode || !$('ckey').hidden || !$('pop').hidden || document.querySelector('dialog[open]:not(.drawer)')) return;
    zoomTo('cluster');
  });
}
