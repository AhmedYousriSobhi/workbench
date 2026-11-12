import { S } from './state.js';
import { dockWide, nodeList } from './inspector.js';
import { paintCrumb } from './focus.js';
import { paintViewSeg } from './shell.js';
import { $, esc } from './util.js';
import { renderCluster } from './cluster.js';
import { buildFlow } from './diagram.js';
import { hasTeleport, scoped, svcList } from './flowstate.js';
import { kindOf, sv } from './svg.js';
import { fitKey, flowSig, loginList } from './geometry.js';


// ---- global view (#321): levels of detail. `overview` condenses a cluster to one card, `full` is the diagram. ----
export const OVERVIEW_AT = 24,
  CLUSTER_NAME = 'workbench';  // above this many nodes the page opens at the overview; the user can pin either level
export const curLevel = () => S.levelPin || (clusterList().length > 1 || (!S.circuit && nodeList().length > OVERVIEW_AT) ? 'overview' : 'full');
export function paintLevelUi() {
  const ov = curLevel() === 'overview';
  paintViewSeg();
  paintCrumb();
}
export function setLevel(l, dive) {
  S.levelPin = l; S.dived = !!dive && l === 'full'; if (l === 'overview') S.activeCluster = null; try { sessionStorage.setItem('workbench-level', l); } catch (e) { /* not remembered */ }
  hidePreview(); try { renderCluster(JSON.parse(S.lastCluster)); } catch (e) { /* no data yet */ } buildFlow(); paintLevelUi(); if (l === 'full') $('flow').focus?.();
}
export const ACCESS_WORD = { ssh: 'direct SSH', teleport: 'Teleport only' };
export const multi = () => { try { return (JSON.parse(S.lastCluster).clusters || []).length > 0; } catch (e) { return false; } };
export function clusterList() { try { const c = JSON.parse(S.lastCluster); return c.clusters && c.clusters.length ? c.clusters.map(x => x.name) : [CLUSTER_NAME]; } catch (e) { return [CLUSTER_NAME]; } }
export const clusterTitle = () => S.activeCluster || clusterList()[0];
export function withCluster(name, fn) { const prev = S.activeCluster; S.activeCluster = multi() ? name : null; try { return fn(); } finally { S.activeCluster = prev; } }
export function enterCluster(name) { S.activeCluster = multi() ? name : null; $('crumb-name').textContent = clusterTitle(); setLevel('full', true); }
export function clusterFacts() {
  const nodes = nodeList(), groups = [['Compute nodes', nodes.filter(n => n.kind === 'compute')], ['GPU nodes', nodes.filter(n => kindOf(n) === 'gpu')], ['Kubernetes nodes', nodes.filter(n => n.kind === 'k8s')]].filter(x => x[1].length);
  const sc = S.lastCluster ? scoped() : {}, svcs = svcList(), snap = S.flowSnap && { ...S.flowSnap, reachable: sc.reachable || S.flowSnap.reachable, faults: sc.faults ?? S.flowSnap.faults }, off = !snap || !Object.values(snap.reachable).some(Boolean);
  const up = svcs.filter(x => snap && snap.reachable[x[0]]).length;
  return { nodes, groups, svcs, snap, off, up, ok: nodes.filter(n => n.state === 'ok').length, faults: snap ? snap.faults || 0 : 0 };
}
export function overviewSummary(f) {
  return `${f.ok} of ${f.nodes.length} nodes healthy · ` + (f.off ? 'services not answering' : `${f.up} of ${f.svcs.length} services answering`) + ` · ${f.faults} ${f.faults === 1 ? 'fault' : 'faults'} applied`;
}
export function showPreview(x, y, name) { withCluster(name, () => showPreviewIn(x, y, name)); }
export function showPreviewIn(x, y, name) {  // the design underneath, as a small diagram; hovering or focusing the card shows it, nothing navigates
  const f = clusterFacts(), lg = loginList(), pv = $('ovprev'), tp = hasTeleport();
  pv.innerHTML = `<b>${esc(name || clusterTitle())}: the design underneath</b><div class="pvrow"><span class="pvb">Clients<small>Monitoring app, Prometheus, curl, you</small></span><i aria-hidden="true">↓</i>
    <span class="pvb">Access<small>${[...(tp ? ['Teleport proxy'] : []), ...lg.map(l => `${esc(l.name)} (${ACCESS_WORD[l.mode] || 'SSH + Teleport'})`)].join(', ')}</small></span><i aria-hidden="true">↓ jump</i>
    <span class="pvb">Nodes<small>${f.groups.map(([t, a]) => `${a.length} ${t.toLowerCase()}`).join(', ') || 'none yet'}</small></span></div>
    <span class="pvb pvs">Services<small>${f.svcs.map(x => esc(x[1]) + (f.snap ? (f.snap.reachable[x[0]] ? ' ✓' : f.off ? ' –' : ' ✗') : '')).join(', ')}</small></span><em>Double-click or press Enter to open</em>`;
  pv.hidden = false; const host = $('trafficpanel').getBoundingClientRect(), w = pv.offsetWidth, h = pv.offsetHeight;
  pv.style.left = Math.max(8, Math.min(x - host.left + 14, host.width - w - 8)) + 'px'; pv.style.top = Math.max(8, Math.min(y - host.top + 14, host.height - h - 8)) + 'px';
}
export function hidePreview() { $('ovprev').hidden = true; }
export function buildCard() {
  const g = $('flow'), f = clusterFacts(), lgs = loginList(), tp = hasTeleport(); g.dataset.ov = '1'; const r = g.getBoundingClientRect();
  g.innerHTML = ''; S.dots = []; S.tiles = {}; S.svcPos = {}; S.svcOut = {}; S.svcLane = {}; S.flowNodes = flowSig(f.nodes); S.flowFitKey = fitKey(); g.removeAttribute('data-fit'); g.style.removeProperty('--u');
  const W = Math.max(320, Math.round(r.width)), rowsEst = Math.ceil(f.svcs.reduce((a, [, l]) => a + l.length * 8 + 52, 0) / (W - 64)), H = Math.max(300, Math.round(dockWide() && r.height > 80 ? r.height : 420 + f.groups.length * 50 + rowsEst * 34 + (W < 900 ? 150 : 0))), wide = W >= 900; g.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const T = (x, y, t, sz, cls, anchor) => sv('text', { class: cls, x, y, style: `font-size:${sz}px`, ...(anchor ? { 'text-anchor': anchor } : {}) }, t);
  const defs = sv('defs', {}), mk = sv('marker', { id: 'arr', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 8, markerHeight: 8, orient: 'auto' }); mk.append(sv('path', { d: 'M0 0 L10 5 L0 10 z' })); defs.append(mk); g.append(defs);
  const x0 = 12, y0 = 12, cw = W - 24, ch = H - 24, card = sv('g', { class: 'ovcard', role: 'button', tabindex: 0, 'aria-label': `${clusterTitle()} cluster: ${overviewSummary(f)}. Press Enter or double-click to open the full design.` });
  const open = () => enterCluster(clusterTitle());
  card.append(sv('rect', { class: 'ovbg', x: x0, y: y0, width: cw, height: ch, rx: 16 }), T(x0 + 20, y0 + 34, clusterTitle() + ' cluster', 22, 'ovt'), T(x0 + 20, y0 + 58, wide ? overviewSummary(f) : overviewSummary(f).split(' · ').slice(0, 1).concat(overviewSummary(f).split(' · ').slice(2)).join(' · '), 15, 'ovd'), ...(wide ? [] : [T(x0 + 20, y0 + 79, overviewSummary(f).split(' · ')[1], 15, 'ovd')]));
  const sy = y0 + ch - 66, pills = [];  // services strip: one pill per service, wrapped; ✓ up, ✗ not reachable, – stack not running
  let px = x0 + 20, py = y0 + ch - 128 + 0; const rowsNeeded = (() => { let x = x0 + 20, rows = 1; for (const [, l] of f.svcs) { const w = l.length * 8 + 44; if (x + w > x0 + cw - 20) { x = x0 + 20; rows++; } x += w + 8; } return rows; })();
  py = y0 + ch - 58 - rowsNeeded * 34; const stripTop = py;
  for (const [k, l] of f.svcs) {
    const up = f.snap && f.snap.reachable[k], mark = !f.snap || f.off ? '–' : up ? '✓' : '✗', w = l.length * 8 + 44;
    if (px + w > x0 + cw - 20) { px = x0 + 20; py += 34; }
    card.append(sv('rect', { class: 'ovpill ' + (f.off ? 'off' : up ? 'up' : 'down'), x: px, y: py, width: w, height: 26, rx: 13 }), T(px + 14, py + 18, `${l} ${mark}`, 14, 'ovpt')); px += w + 8;
  }
  const bTop = y0 + (wide ? 74 : 94), bBot = stripTop - 14, fh = Math.max(120, bBot - bTop), gap = 34;
  let C, A, N;
  const accNames = [...(tp ? ['Teleport proxy'] : []), ...lgs.map(l => l.name)], mc = Math.max(12, Math.floor((cw - 68) / 7.6));
  const wrapList = items => { const out = []; let cur = ''; for (const it of items) { if (cur && (cur + ' · ' + it).length > mc) { out.push(cur); cur = it; } else cur = cur ? cur + ' · ' + it : it; } if (cur) out.push(cur); return out; };
  const cliLines = wrapList(['Monitoring app', 'Prometheus', 'curl', 'you']), accWrap = wrapList(accNames);
  if (wide) { const use = cw - 40 - 2 * gap, w1 = use * 0.2, w2 = use * 0.3, w3 = use * 0.5, xa = x0 + 20; C = { x: xa, y: bTop + fh / 2 - Math.min(fh, 150) / 2, w: w1, h: Math.min(fh, 150) }; A = { x: xa + w1 + gap, y: bTop, w: w2, h: fh }; N = { x: A.x + w2 + gap, y: bTop, w: w3, h: fh }; }
  else {  // stacked: each box is as tall as its content needs; spare height becomes even gaps
    const hC = 34 + cliLines.length * 21, hA = 34 + accWrap.length * 21, hN = 38 + f.groups.length * 50, gp = Math.max(18, Math.min(60, (fh - hC - hA - hN) / 2)), y1 = bTop + Math.max(0, (fh - hC - hA - hN - 2 * gp) / 2);
    C = { x: x0 + 20, y: y1, w: cw - 40, h: hC }; A = { x: C.x, y: y1 + hC + gp, w: C.w, h: hA }; N = { x: C.x, y: A.y + hA + gp, w: C.w, h: hN };
  }
  const bx = (b, title, lines) => { card.append(sv('rect', { class: 'ovbox', x: b.x, y: b.y, width: b.w, height: b.h, rx: 10 }), T(b.x + 14, b.y + 26, title, 16, 'ovt')); lines.forEach((t, i) => card.append(T(b.x + 14, b.y + 50 + i * 21, t, 14, 'ovd'))); };
  const arrow = (a, b) => card.append(sv('path', { class: 'ovarr', d: `M${a.x} ${a.y} L${b.x} ${b.y}`, 'marker-end': 'url(#arr)' }));
  bx(C, 'Clients', wide ? ['Monitoring app', 'Prometheus', 'curl · you (ssh, tsh)'].slice(0, Math.max(1, Math.floor((C.h - 34) / 21))) : cliLines);
  const accLines = [...(tp ? ['Teleport proxy'] : []), ...lgs.map(l => `${l.name} · ${ACCESS_WORD[l.mode] || 'SSH + Teleport'}`)];
  bx(A, 'Access', wide ? accLines.slice(0, Math.max(1, Math.floor((A.h - 34) / 21))) : accWrap);
  card.append(sv('rect', { class: 'ovbox', x: N.x, y: N.y, width: N.w, height: N.h, rx: 10 }), T(N.x + 14, N.y + 26, 'Cluster nodes', 16, 'ovt'));
  const gh = wide ? (N.h - 40) / Math.max(1, f.groups.length) : 50;
  f.groups.forEach(([title, arr], i) => {
    const gy = N.y + 40 + i * gh, okc = arr.filter(n => n.state === 'ok').length, bw = N.w - 28, sw = bw / arr.length, gp = sw > 5 ? 1.5 : 0;
    card.append(T(N.x + 14, gy + 16, `${title}: ${okc} of ${arr.length} healthy`, 14, 'ovd'));
    arr.forEach((n, j) => card.append(sv('rect', { class: 'ovseg ' + (n.state === 'ok' ? 'ok' : 'bad'), x: N.x + 14 + j * sw, y: gy + 26, width: Math.max(1.5, sw - gp), height: n.state === 'ok' ? 16 : 22, rx: 2 }, null)));
  });
  if (wide) card.append(T(x0 + 20, y0 + ch - 20, 'Hover to see the design underneath', 13, 'ovd'));
  const ob = sv('g', { class: 'ovopen', role: 'button', tabindex: 0, 'aria-label': 'Open the full design of ' + clusterTitle() }); ob.append(sv('rect', { x: x0 + cw - 190, y: y0 + ch - 44, width: 170, height: 32, rx: 8 }), T(x0 + cw - 105, y0 + ch - 23, 'Open full design ▸', 14, 'ovot', 'middle'));
  ob.onclick = e => { e.stopPropagation(); open(); }; ob.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); open(); } };
  card.append(ob);
  if (wide) { arrow({ x: C.x + C.w, y: C.y + C.h / 2 }, { x: A.x - 2, y: A.y + A.h / 2 }); arrow({ x: A.x + A.w, y: A.y + A.h / 2 }, { x: N.x - 2, y: N.y + N.h / 2 }); }
  else { arrow({ x: C.x + C.w / 2, y: C.y + C.h }, { x: A.x + A.w / 2, y: A.y - 2 }); arrow({ x: A.x + A.w / 2, y: A.y + A.h }, { x: N.x + N.w / 2, y: N.y - 2 }); }
  card.ondblclick = open; card.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
  card.onclick = () => { if (matchMedia('(max-width:640px)').matches) open(); };  // a phone has no hover and no double tap: one tap enters
  card.onmousemove = e => showPreview(e.clientX, e.clientY, clusterTitle()); card.onmouseleave = hidePreview;
  card.onfocus = () => { const b = card.getBoundingClientRect(); showPreview(b.left + b.width / 3, b.top + b.height / 3, clusterTitle()); }; card.onblur = hidePreview;
  g.append(card);
}
export const ovSig = () => JSON.stringify(clusterList().map(n => withCluster(n, () => { const f = clusterFacts(); return [n, f.nodes.map(x => x.name + x.state).join(), f.up, f.off, f.faults, hasTeleport()]; })));
export function buildOverview() {  // one card, or a grid of cards when there are several clusters (#327)
  const names = clusterList(), g = $('flow'); g.style.removeProperty('height');
  if (names.length === 1) withCluster(names[0], buildCard); else buildGrid(names);
  S.lastOvSig = ovSig();
  $('ovtable').innerHTML = `<table><caption>Clusters</caption><thead><tr><th>Cluster</th><th>Nodes healthy</th><th>Services answering</th><th>Faults applied</th></tr></thead><tbody>` + names.map(n => withCluster(n, () => { const f = clusterFacts(); return `<tr><td><button class="linkbtn" data-c="${esc(n)}">${esc(n)}</button></td><td>${f.ok} of ${f.nodes.length}</td><td>${f.off ? 'none (stack not running)' : f.up + ' of ' + f.svcs.length}</td><td>${f.faults}</td></tr>`; })).join('') + '</tbody></table>';
  $('ovtable').querySelectorAll('button[data-c]').forEach(b => b.onclick = () => enterCluster(b.dataset.c));
}
export function refreshOverview() { if (ovSig() !== S.lastOvSig) buildOverview(); }  // states and counts change without the node list changing
export function buildGrid(names) {
  const g = $('flow'), r = g.getBoundingClientRect(); g.dataset.ov = '1'; g.innerHTML = ''; S.dots = []; S.tiles = {}; S.svcPos = {}; S.svcOut = {}; S.svcLane = {}; S.flowNodes = flowSig(nodeList()); S.flowFitKey = fitKey(); g.removeAttribute('data-fit'); g.style.removeProperty('--u');
  const N = names.length, gap = 14, W = Math.max(320, Math.round(r.width)), room = dockWide() && r.height > 80 ? Math.round(r.height) : 0, MINW = 210, MINH = 128;
  let best = null;  // columns that give the biggest cards, none smaller than the minimum; if they do not all fit the panel the drawing grows and the panel scrolls
  for (let c = 1; c <= N; c++) { const rows = Math.ceil(N / c), w = (W - 24 - (c - 1) * gap) / c, h = room ? (room - 24 - (rows - 1) * gap) / rows : 190; if (w < MINW && c > 1) break; const sc = Math.min(w, h * 1.7); if (!best || sc > best.sc) best = { c, rows, w, h, sc }; }
  const cols = best.c, rows = best.rows, cwid = best.w, chgt = Math.max(MINH, best.h), H = Math.round(rows * chgt + (rows - 1) * gap + 24);
  g.setAttribute('viewBox', `0 0 ${W} ${H}`); if (H > room && room) g.style.height = H + 'px';
  const T = (x, y, t, sz, cls, anchor) => sv('text', { class: cls, x, y, style: `font-size:${sz}px`, ...(anchor ? { 'text-anchor': anchor } : {}) }, t);
  const defs = sv('defs', {}), mk = sv('marker', { id: 'arr', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 8, markerHeight: 8, orient: 'auto' }); mk.append(sv('path', { d: 'M0 0 L10 5 L0 10 z' })); defs.append(mk); g.append(defs);
  names.forEach((name, i) => withCluster(name, () => {
    const f = clusterFacts(), lg = loginList(), tp = hasTeleport(), x = 12 + (i % cols) * (cwid + gap), y = 12 + Math.floor(i / cols) * (chgt + gap), bad = f.nodes.length - f.ok > 0 || (!f.off && f.up < f.svcs.length);
    const card = sv('g', { class: 'ovcard' + (bad ? ' attn' : ''), role: 'button', tabindex: 0, 'aria-label': `${name}: ${overviewSummary(f)}. Press Enter or double-click to open.` }), open = () => enterCluster(name);
    card.append(sv('rect', { class: 'ovbg', x, y, width: cwid, height: chgt, rx: 14 }), T(x + 14, y + 26, (bad ? '▲ ' : '') + name, 17, 'ovt'), T(x + 14, y + 48, `${f.ok} of ${f.nodes.length} nodes healthy`, 13, 'ovd'), T(x + 14, y + 66, (f.off ? 'services not answering' : `${f.up} of ${f.svcs.length} services`) + ` · ${f.faults} ${f.faults === 1 ? 'fault' : 'faults'}`, 13, 'ovd'));
    const ob = sv('g', { class: 'ovopen', role: 'button', tabindex: 0, 'aria-label': 'Open ' + name }); ob.append(sv('rect', { x: x + cwid - 74, y: y + 10, width: 62, height: 24, rx: 6 }), T(x + cwid - 43, y + 27, 'Open ▸', 13, 'ovot', 'middle'));
    ob.onclick = e => { e.stopPropagation(); open(); }; ob.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); open(); } }; card.append(ob);
    const bw = cwid - 28, all = f.nodes, sw = all.length ? bw / all.length : bw, seg = (arr, bx, by, w2) => { const s2 = arr.length ? w2 / arr.length : w2; arr.forEach((n, j) => card.append(sv('rect', { class: 'ovseg ' + (n.state === 'ok' ? 'ok' : 'bad'), x: bx + j * s2, y: by, width: Math.max(1.5, s2 - (s2 > 5 ? 1.5 : 0)), height: n.state === 'ok' ? 12 : 18, rx: 2 })))};
    if (chgt >= 150 && cwid >= 300) {  // roomy: [login] -> [nodes], the condensed design of the cluster
      const by = y + 80, bh = chgt - 92, aw = Math.min(110, cwid * 0.3), gx = 30, nx = x + 14 + aw + gx, nw = x + cwid - 14 - nx;
      card.append(sv('rect', { class: 'ovbox', x: x + 14, y: by, width: aw, height: bh, rx: 8 }), T(x + 24, by + 22, 'Access', 13, 'ovt'), T(x + 24, by + 40, `${lg.length} login${tp ? ' + Teleport' : ''}`.slice(0, 18), 12, 'ovd'),
        sv('path', { class: 'ovarr', d: `M${x + 14 + aw} ${by + bh / 2} L${nx - 2} ${by + bh / 2}`, 'marker-end': 'url(#arr)' }), sv('rect', { class: 'ovbox', x: nx, y: by, width: nw, height: bh, rx: 8 }));
      f.groups.forEach(([t, arr], k) => { const gy = by + 8 + k * Math.max(36, (bh - 12) / f.groups.length); card.append(T(nx + 10, gy + 14, `${t.split(' ')[0]} ${arr.filter(n => n.state === 'ok').length}/${arr.length}`, 12, 'ovd')); seg(arr, nx + 10, gy + 20, nw - 20); });
    } else { seg(all, x + 14, y + chgt - 28, bw); }
    card.ondblclick = open; card.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
    card.onclick = () => { if (matchMedia('(max-width:640px)').matches) open(); };
    card.onmousemove = e => showPreview(e.clientX, e.clientY, name); card.onmouseleave = hidePreview; card.onfocus = () => { const b = card.getBoundingClientRect(); showPreview(b.left + b.width / 3, b.top + b.height / 3, name); }; card.onblur = hidePreview;
    g.append(card);
  }));
}

export function init() {
  try { S.levelPin = sessionStorage.getItem('workbench-level'); } catch (e) { /* not remembered */ }

}
