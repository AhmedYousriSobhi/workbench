import { S } from './state.js';
import { G, reduced, svcList } from './flowstate.js';
import { $, esc } from './util.js';
import { at, curve, sv } from './svg.js';
import { paintKpis } from './cluster.js';
import { blastFor, setBlast } from './blast.js';
import { paintEye } from './lens.js';
import { buildFlow, flowNote, paintBlocks } from './diagram.js';
import { paintTraffic } from './traffic.js';
import { paintRunEye } from './testrun.js';
import { fitKey } from './geometry.js';

export function animating() { return !S.flowPaused && !reduced.matches && S.flowLevel < 3 && !document.hidden; }
export function launch(ev) {
  if (!S.svcPos[ev.to]) return;
  const sp = S.svcPos[ev.to], fault = ev.from === 'injector', origin = fault ? { x: G.injector.x + G.injector.w, y: G.injector.y + G.injector.h / 2 } : S.svcOut[ev.to] || { x: G.clients.x + G.clients.w, y: G.clients.y + G.clients.h / 2 };
  const targets = fault && ev.nodes?.some(n => S.tiles[n]) ? ev.nodes.filter(n => S.tiles[n]).map(n => ({ pt: { x: S.tiles[n].x, y: S.tiles[n].y + S.tiles[n].h / 2 }, node: n })) : [{ pt: ev.to === 'teleport' ? { x: sp.x, y: sp.y + sp.h / 2 } : sp.entry || { x: sp.x + sp.w / 2, y: sp.y }, node: null }];
  for (const t of targets) for (let i = 0; i < Math.min(ev.n, 5) && S.dots.length < 60; i++) {
    const err = !fault && i < Math.min(ev.err || 0, 5);  // an answer the mock counted as an error is a triangle, not a circle (#314); shape carries it, colour only helps
    const el = fault ? sv('rect', { class: 'pk', width: 14, height: 14, rx: 2 }) : err ? sv('polygon', { class: 'pk err', points: '0,-10 10,8 -10,8' }) : sv('circle', { class: 'pk', r: 7 }), trail = sv('circle', { class: 'pkt', r: 5 });
    $('dots').append(trail, el);
    S.dots.push({ el, trail, fault, err, bez: !fault && S.svcLane[ev.to] ? S.svcLane[ev.to] : curve(origin, t.pt), node: t.node, svc: ev.to, t0: performance.now() + i * 130, dur: fault ? 1700 : 1200 });
  }
  if (!S.raf) S.raf = requestAnimationFrame(step);
}
export function hit(d) {
  const t = d.node && S.tiles[d.node]; if (!t) return;
  const r = sv('rect', { class: 'ring', x: t.x, y: t.y, width: t.w, height: t.h, rx: 5 }); t.g.append(r); setTimeout(() => r.remove(), 1200);
}
export function step(now) {
  S.dots = S.dots.filter(d => {
    const p = (now - d.t0) / d.dur;
    if (p >= 1) { d.el.remove(); d.trail.remove(); if (d.fault) hit(d); return false; }
    const q = Math.max(p, 0), pt = at(d.bez, q), tr = at(d.bez, Math.max(q - 0.07, 0)), vis = p < 0 ? 'hidden' : 'visible';
    d.el.setAttribute('visibility', vis); d.trail.setAttribute('visibility', vis); d.trail.setAttribute('cx', tr.x); d.trail.setAttribute('cy', tr.y);
    if (d.fault) { d.el.setAttribute('transform', `translate(${pt.x} ${pt.y}) rotate(45) translate(-7 -7)`); } else if (d.err) { d.el.setAttribute('transform', `translate(${pt.x} ${pt.y})`); } else { d.el.setAttribute('cx', pt.x); d.el.setAttribute('cy', pt.y); }
    return true;
  });
  S.raf = S.dots.length ? requestAnimationFrame(step) : 0;
}
export function onFlow(snap) {
  S.flowLevel = snap.level; S.flowCursor = snap.cursor;
  snap.faults = (S.flowSnap?.faults || 0) + snap.events.filter(e => e.from === 'injector').length;
  snap.events.forEach(e => S.recent.push({ t: Date.now(), to: e.to, n: e.from === 'clients' ? e.n : 0, fault: e.from === 'injector' }));
  snap.events.forEach(e => S.trHist.push({ t: Date.now(), from: e.from, to: e.to, n: e.n, label: e.label, nodes: e.nodes, err: e.err, ms: snap.latency?.[e.to] }));
  S.trHist = S.trHist.filter(h => h.t > Date.now() - 3600e3).slice(-800);
  S.flowSnap = snap; paintBlocks(); paintKpis(); flowNote(); paintFeed(snap.events); paintTraffic(); paintRunEye();
  if (S.lens === 'client') paintEye();
  const inj = snap.events.filter(e => e.from === 'injector').pop(); if (inj) { const b = blastFor(inj); if (b) setBlast(b); }
  if (animating()) snap.events.forEach(launch);
}
export const feed = [];  // the Timeline dock: what the page was told, newest first (text only; the dots are the animation)
export function paintFeed(events) {
  const name = k => (svcList().find(x => x[0] === k) || [k, k])[1], hh = d => d.toTimeString().slice(0, 8);
  for (const e of events) {
    const fault = e.from === 'injector';
    feed.unshift({ t: new Date(), fault, text: fault ? `Fault ${e.label || 'applied'} on ${(e.nodes || []).length ? e.nodes.join(', ') : name(e.to)}` : `${e.n} ${e.n === 1 ? 'request' : 'requests'} to ${name(e.to)}` + (e.err ? `, ${e.err} ${e.err === 1 ? 'error' : 'errors'}` : '') });
  }
  feed.length = Math.min(feed.length, 40); $('dock-empty').hidden = feed.length > 0;
  $('timeline-feed').innerHTML = feed.map(f => `<li><span class="ft">${hh(f.t)}</span><span><b class="${f.fault ? 'f' : 'r'}" aria-hidden="true">${f.fault ? '◆' : '●'}</b> ${esc(f.text)}</span></li>`).join('');
}
export function openFlow() {
  if (S.flowEs) return;
  if (!$('flow').firstChild) buildFlow();
  S.flowEs = new EventSource('/api/flow?after=' + (S.flowCursor || ''));
  S.flowEs.onmessage = e => onFlow(JSON.parse(e.data));
}
export function closeFlow() { if (S.flowEs) S.flowEs.close(); S.flowEs = null; S.flowCursor = 0; S.dots.forEach(d => { d.el.remove(); d.trail.remove(); }); S.dots = []; }

export function init() {
  new ResizeObserver(() => { clearTimeout(fitTimer); fitTimer = setTimeout(() => { if ($('flow').firstChild && fitKey() !== S.flowFitKey) buildFlow(); }, 150); }).observe($('flow').parentElement);  // the panel changed shape (window, node selected): fit again
  let fitTimer = 0;
  $('flowpause').onclick = () => { S.flowPaused = !S.flowPaused; $('flowpause').setAttribute('aria-pressed', S.flowPaused); $('flowpause').textContent = S.flowPaused ? 'Resume' : 'Pause'; $('flowpause').setAttribute('aria-label', S.flowPaused ? 'Resume animation' : 'Pause animation'); flowNote(); };

}
