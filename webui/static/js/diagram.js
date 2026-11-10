import { S } from './state.js';
import { applyFocus, focusOn, tagEntities } from './focus.js';
import { applyBlast } from './blast.js';
import { applyLens } from './lens.js';
import { $ } from './util.js';
import { buildOverview, curLevel, refreshOverview, setLevel } from './overview.js';
import { STATE_ICON } from './cluster.js';
import { ACC_PORT, G, REAL_SERVICES, accExtra, accUp, hasService, reduced, svcFit, svcList } from './flowstate.js';
import { at, curve, dpath, poly, sv } from './svg.js';
import { CLIENT_INFO, asButton, clusterNow, nodeList, openPop } from './inspector.js';
import { KIND_NAME, MODE_TEXT, SVC_ICON, SVC_METHOD, UF, fitKey, fitText, flowFit, flowGeom, flowSig, loginAddr, loginKind, loginList, loginRuns, portLine, stackUp, textW } from './geometry.js';

export function buildFlow() { buildFlowBase(); tagEntities(); applyFocus(); applyBlast(); applyLens(); }
export function buildFlowBase() {
  if (curLevel() === 'overview') { buildOverview(); return; }
  $('ovtable').innerHTML = ''; delete $('flow').dataset.ov;
  const g = $('flow'), nodes = nodeList(), lgs = loginList(), svcs = svcList().filter(x => x[0] !== 'teleport'), tpBox = svcList().find(x => x[0] === 'teleport');
  g.innerHTML = ''; S.dots = []; S.tiles = {}; S.svcPos = {}; S.svcOut = {}; S.svcLane = {}; S.flowNodes = flowSig(nodes); S.flowFitKey = fitKey();
  const CHIPS = [['Monitoring app', lgs[0].name], ['Prometheus', 'prometheus'], ['curl · scripts', null], ['You: ssh · tsh', lgs[0].name]];
  const acc = [...(tpBox ? [tpBox] : []), ...accExtra(), ...lgs], fit = flowFit(), col = fit.col, nS = svcs.length;
  let u = S.unitNow, P;  // drawing units per screen pixel: the drawing is laid out, measured against the panel, and laid out again until the text size settles
  const r = g.getBoundingClientRect();
  for (let it = 0; it < 5; it++) {
    P = flowGeom(nodes, acc, svcs, fit.a, col, u);
    if (!(r.width > 80)) break;
    const sc = col ? Math.min(r.width / P.FW, r.height / P.H) : r.width / P.FW, nu = Math.min(2.6, Math.max(0.8, 1 / sc));
    if (Math.abs(nu - u) < 0.03) break;
    u += (nu - u) * 0.7;
  }
  P = flowGeom(nodes, acc, svcs, fit.a, col, u); S.unitNow = u; const { mA, mS } = P;  // the last layout uses the size the CSS will use
  G.clients = P.clients; G.injector = P.injector; G.cluster = P.cluster;
  g.setAttribute('viewBox', `0 0 ${P.FW} ${P.H}`); g.setAttribute('data-fit', col ? 'c' : 'n'); g.style.setProperty('--u', u.toFixed(3));
  const fr = (rc, rx) => sv('rect', { class: 'frame', x: rc.x, y: rc.y, width: rc.w, height: rc.h, rx });
  const defs = sv('defs', {}), mk = sv('marker', { id: 'arr', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 8, markerHeight: 8, orient: 'auto' }); mk.append(sv('path', { d: 'M0 0 L10 5 L0 10 z' })); defs.append(mk);
  for (const [id, a, b] of [['slab', 'gs-t', 'gs-b'], ['sim', 'gm-t', 'gm-b'], ['real', 'gr-t', 'gr-b'], ['frame', 'gf-t', 'gf-b']]) { const lg = sv('linearGradient', { id: 'g-' + id, x1: 0, y1: 0, x2: 0, y2: 1 }); lg.append(sv('stop', { offset: 0, class: a }), sv('stop', { offset: 1, class: b })); defs.append(lg); }
  g.append(defs);
  g.append(fr(P.clients, 12), fr(P.injector, 12), fr(P.access, 14), fr(P.svcFrame, 14), fr(P.cluster, 14));
  const jy = P.injector.y, cy = P.clients.y, ix = P.injector.x + 14, tb = P.tb, sub = UF.sub * u, W = (t, k, w, up) => textW(t, k, u, w, up);
  g.append(sv('text', { class: 'ftitle', x: P.clients.x + 14, y: cy + tb }, 'Clients'), sv('text', { class: 'ftitle', x: ix, y: jy + tb }, 'Faults'), sv('text', { class: 'ftitle', x: P.access.x + 16, y: P.access.y + tb }, 'Access: how users get in'),
    sv('text', { class: 'ftitle', x: P.svcFrame.x + 16, y: col ? P.svcFrame.y + tb : P.svcFrame.y + P.svcFrame.h - 12 }, col ? 'Services' : 'Services: what clients call'), sv('text', { class: 'ftitle', x: P.cluster.x + 16, y: P.cluster.y + tb }, 'Cluster nodes'));
  g.append(sv('text', { class: 'sub', id: 'fc-clients', x: col ? P.clients.x + 14 + W('Clients', 'title', 700, true) + 16 : 22, y: col ? cy + tb : P.clients.y + P.clients.h - 12 }, ''),
    sv('text', { class: 'sub', id: 'fc-injector', x: ix + (col ? W('Faults', 'title', 700, true) + 16 : 0), y: col ? jy + tb : jy + 110 }, ''),
    sv('text', { class: 'sub', x: ix, y: jy + (col ? tb + sub * 1.9 : 58) }, '◆ scenarios'), sv('text', { class: 'sub', x: ix + (col ? W('◆ scenarios', 'sub') + 18 : 0), y: col ? jy + tb + sub * 1.9 : jy + 84 }, '◆ buttons'));
  const nOut = (col ? 0 : (tpBox ? 1 : 0) + accExtra().length + lgs.filter(l => l.mode !== 'teleport').length) + nS; let oi = 0;  // lines leave the client frame spread along its edge, so they do not pile up
  const out = () => col ? { x: P.clients.x + P.clients.w, y: P.clients.y + 30 + (oi++) * ((P.clients.h - 50) / Math.max(nS - 1, 1)) } : { x: P.clients.x + P.clients.w, y: P.clients.y + 44 + (oi++) * ((P.clients.h - 96) / Math.max(nOut - 1, 1)) };
  const injOut = { x: P.injector.x + (col ? 0 : P.injector.w), y: P.injector.y + P.injector.h / 2 };
  const lanes = sv('g', {}), labels = sv('g', { class: 'elabs' }); g.append(lanes);
  const gr = clusterNow().graph, gedge = eid => gr && gr.edges.find(e => e.id === eid);  // the declared graph (webui/graph.py, #312) decides which lines exist
  const edge = (kind, bz, text, tt = 0.5, id, eid) => {  // a labelled line: `kind` says the method (ssh, teleport, jump, api), the label says it in words; `eid` is its edge in the model
    const me = eid && gedge(eid); if (gr && !me) return bz;
    lanes.append(sv('path', { class: 'lane ' + kind, d: dpath(bz), ...(id ? { id } : {}), ...(me ? { 'data-edge': me.id, 'data-plane': me.plane, 'data-measured': me.measured } : {}), ...(kind !== 'api' ? { 'marker-end': 'url(#arr)' } : {}) }));
    if (text) { const m = at(bz, tt), w = W(text, 'lab', 600) + 16, h = UF.lab * u * 1.6; labels.append(sv('rect', { class: 'elbg', x: m.x - w / 2, y: m.y - h / 2, width: w, height: h, rx: 6 }), sv('text', { class: 'elab', x: m.x, y: m.y + UF.lab * u * 0.36, 'text-anchor': 'middle' }, text)); }
    return bz;
  };
  const box = (k, label, subtxt, p, popKind, popId, cls = '') => {
    S.svcPos[k] = Object.assign(S.svcPos[k] || {}, p);
    const m = p.h === mS.h ? mS : mA, bg = asButton(sv('g', {}), `${label}${REAL_SERVICES.has(k) ? ' (real)' : cls === 'blkacc' ? '' : ' (simulated)'}: details`, () => openPop(bg, popKind, popId), () => svTip(k));
    const kind = cls === 'blkacc' ? '' : REAL_SERVICES.has(k) ? 'real' : 'sim', bx = UF.sub * u;
    bg.append(sv('rect', { class: 'blk ' + cls + (kind ? ' ' + kind : ''), id: 'fb-' + k, x: p.x, y: p.y, width: p.w, height: p.h, rx: 10 }), sv('rect', { class: 'fring', id: 'ff-' + k, x: p.x - 4, y: p.y - 4, width: p.w + 8, height: p.h + 8, rx: 13 }), ...(kind ? [sv('path', { class: 'kbar ' + kind, d: `M${p.x + 4} ${p.y + 12} V${p.y + p.h - 12}` })] : []),
      sv('text', { class: 'badge act', id: 'fa-' + k, x: p.x + p.w - bx * 2.4, y: p.y + m.ys[0] }, '●'), sv('text', { class: 'badge flt', id: 'fx-' + k, x: p.x + p.w - bx * 1.2, y: p.y + m.ys[0] }, '▲'),
      sv('text', { class: 'sn', x: p.x + 12, y: p.y + m.ys[0] }, label), sv('text', { class: 'sub', id: 'fs-' + k, x: p.x + 12, y: p.y + m.ys[1] }, ''), sv('text', { class: 'sub', id: 'fc-' + k, x: p.x + 12, y: p.y + m.ys[2] }, subtxt)); svcFit[k] = p.w - 24; const ic = SVC_ICON[k]; if (ic) { const z = Math.min(p.h * 0.4, 30); bg.append(sv('path', { class: 'sico', d: ic, 'aria-hidden': 'true', transform: `translate(${p.x + p.w - z - 8} ${p.y + p.h - z - 6}) scale(${z / 24})` })); } return bg;
  };
  const accBox = (b, p) => {  // the jump host and the controller: a box like a login node, with its state in words as well as in the outline
    const k = b[0], up = accUp(k), word = up ? '✓ up' : stackUp() ? '✗ not reachable' : '– offline', addr = `ssh · 127.0.0.1:${ACC_PORT[k]}`;
    const bg = asButton(sv('g', { id: 'ac-' + k }), `${b[1]}: ${word}. Details`, () => openPop(bg, 'acc', k), () => [`${b[1]} · ${word}`, k === 'jump' ? 'A bare ssh bastion · click for the address and command' : 'slurmctld host, admins only · click for the address and command']);
    const lines = col ? [b[1], word, b[2], addr] : [b[1], word, addr];
    bg.append(sv('rect', { class: 'blk lgn' + (up ? '' : stackUp() ? ' down' : ' off'), x: p.x, y: p.y, width: p.w, height: p.h, rx: 10 }), ...lines.map((t, n) => sv('text', { class: n ? 'sub' : 'sn', x: p.x + 12, y: p.y + mA.ys[n] }, fitText(t, n ? 'sub' : 'sn', u, p.w - 24, n ? 400 : 600))));
    return bg;
  };
  const svTip = k => { const l = svcList().find(x => x[0] === k); const up = S.flowSnap?.reachable[k]; return [l[1] + (REAL_SERVICES.has(k) ? ' (real)' : k === 'teleport' ? '' : ' (simulated)') + ' · ' + (up ? 'up' : S.flowSnap ? 'not reachable' : 'checking'), SVC_METHOD[k] + ' · click for address and command']; };
  const place = acc.map((a2, i) => [a2, P.place[i]]), tpP = tpBox && place[0][1], cb = P.clients.y + P.clients.h;
  let uu = 0, hh = 0;
  if (col) {  // Access row: straight lines down from the Clients frame; Teleport to a login runs in a U under the boxes; every box has a short jump line down to the nodes
    if (tpBox) { const o = S.svcOut.teleport = { x: tpP.x + tpP.w / 2, y: cb }; S.svcLane.teleport = edge('api', poly([o, { x: o.x, y: tpP.y }]), SVC_METHOD.teleport, 0.5, 'ln-teleport', 'clients>teleport:api'); }
    for (const [l, p] of place) {
      if (Array.isArray(l)) continue;
      if (l.mode !== 'teleport') edge('ssh', poly([{ x: p.x + p.w / 2, y: cb }, { x: p.x + p.w / 2, y: p.y }]), 'direct SSH', 0.5, null, `clients>${l.name}:ssh`);
      if (l.mode !== 'ssh' && tpBox) { const by = p.y + p.h, uy = by + 20 + (uu++) * 26, sx = tpP.x + tpP.w / 2 - 14; edge('teleport', poly([{ x: sx, y: tpP.y + tpP.h }, { x: sx, y: uy }, { x: p.x + p.w / 2 - 14, y: uy }, { x: p.x + p.w / 2 - 14, y: by }]), '', 0.5, null, `teleport>${l.name}:teleport`); }
    }
    for (const [l, p] of place) {  // an s-login jumps to the Slurm nodes; a k-login reaches the Kubernetes nodes with kubectl; a login serving both draws both
      if (Array.isArray(l)) continue;
      for (const [eid, jx, text] of [[`${l.name}>nodes:jump`, p.x + p.w / 2 + 22, 'jump host'], [`${l.name}>k8s-nodes:kubectl`, p.x + p.w / 2 - 22, 'kubectl']]) {
        if (gr && !gedge(eid)) continue;
        lanes.append(sv('path', { class: 'lanehalo', d: dpath(poly([{ x: jx, y: p.y + p.h }, { x: jx, y: P.access.y + P.access.h }])) })); edge('jump', poly([{ x: jx, y: p.y + p.h }, { x: jx, y: P.cluster.y }]), text, 0.5, null, eid);
      }
    }
    if (tpBox) lanes.append(sv('path', { class: 'lanehalo', d: dpath(poly([{ x: tpP.x + tpP.w / 2 + 22, y: tpP.y + tpP.h }, { x: tpP.x + tpP.w / 2 + 22, y: P.access.y + P.access.h }])) })), edge('teleport', poly([{ x: tpP.x + tpP.w / 2 + 22, y: tpP.y + tpP.h }, { x: tpP.x + tpP.w / 2 + 22, y: P.cluster.y }]), 'Teleport', 0.5, null, 'teleport>nodes:teleport');
  } else {
    if (tpBox) { const o = S.svcOut.teleport = out(); S.svcLane.teleport = edge('api', curve(o, { x: tpP.x, y: tpP.y + tpP.h / 2 }), SVC_METHOD.teleport, 0.5, 'ln-teleport', 'clients>teleport:api'); }
    for (const [l, p] of place) {
      if (Array.isArray(l)) continue;
      const mid = p.y + p.h / 2;
      if (l.mode !== 'teleport') edge('ssh', curve(out(), { x: p.x, y: mid - 12 }), 'direct SSH', 0.3, null, `clients>${l.name}:ssh`);
      if (l.mode !== 'ssh' && tpBox) { const s = { x: tpP.x, y: tpP.y + tpP.h / 2 + 12 }, e = { x: p.x, y: mid + 12 }; edge('teleport', [s, { x: s.x - 62, y: s.y }, { x: e.x - 62, y: e.y }, e], 'Teleport', 0.5, null, `teleport>${l.name}:teleport`); }
    }
  }
  const posOf = id => (place.find(([x]) => Array.isArray(x) ? x[0] === id : x.name === id) || [])[1], keyLogins = lgs.filter(l => l.mode !== 'teleport');
  const hop = (src, dst, eid, text) => {  // ssh from one Access box to another (through the jump host, or login to controller): under the boxes, or down the left side when stacked
    const a = posOf(src), b = posOf(dst); if (!a || !b) return;
    if (col) { const by = a.y + a.h, uy = by + 20 + (uu++) * 26, sx = a.x + a.w / 2 + 6, ex = b.x + b.w / 2 + 6; edge('jump', poly([{ x: sx, y: by }, { x: sx, y: uy }, { x: ex, y: uy }, { x: ex, y: b.y + b.h }]), text, 0.5, null, eid); }
    else { const lx = a.x - 34 - (hh++) * 7, s = { x: a.x, y: a.y + a.h / 2 + 4 }, e = { x: b.x, y: b.y + b.h / 2 + 4 }; edge('jump', [s, { x: lx, y: s.y }, { x: lx, y: e.y }, e], text, 0.5, null, eid); }
  };
  for (const b of accExtra()) {  // a line from the clients to the jump host and to the controller, then the hops the declared graph has
    const p = posOf(b[0]), eid = `clients>${b[0]}:ssh`;
    if (col) edge('ssh', poly([{ x: p.x + p.w / 2, y: cb }, { x: p.x + p.w / 2, y: p.y }]), 'ssh', 0.5, null, eid);
    else edge('ssh', curve(out(), { x: p.x, y: p.y + p.h / 2 - 12 }), 'ssh', 0.3, null, eid);
  }
  if (hasService('jump')) { for (const l of keyLogins) hop('jump', l.name, `jump>${l.name}:jump`, 'ssh -J'); if (hasService('controller')) hop('jump', 'controller', 'jump>controller:jump', 'ssh -J'); }
  if (hasService('controller')) for (const l of keyLogins) hop(l.name, 'controller', `${l.name}>controller:jump`, 'ssh');
  const outs = svcs.map(() => out()); if (!col) outs.reverse();  // stacked: the rightmost box takes the topmost origin; column: the top box does. Either way no two service lines cross
  svcs.forEach(([k], i) => {
    const p = P.svcP[i], o = S.svcOut[k] = outs[i];
    if (col) {  // a line leaves the Clients frame to the right, turns down in the gutter and enters its box from the left; faults get a dotted one
      const gx = P.gutter.x1 - 20 - i * 14, ey = p.y + p.h / 2 - 8;
      S.svcPos[k] = Object.assign(p, { entry: { x: p.x, y: ey } });
      S.svcLane[k] = edge('api', poly([o, { x: gx, y: o.y }, { x: gx, y: ey }, { x: p.x, y: ey }]), '', 0.5, 'ln-' + k, `clients>${k}:api`);
      if (!gr || gedge(`faults>${k}:inj`)) lanes.append(sv('path', { class: 'lane inj', 'data-edge': `faults>${k}:inj`, 'data-plane': 'faults', 'data-measured': true, d: dpath(poly([injOut, { x: gx - 6, y: injOut.y }, { x: gx - 6, y: ey + 16 }, { x: p.x, y: ey + 16 }])), opacity: 0.3 }));
    } else {  // a line from the clients runs down the gutter, along the channel above the band, and drops into the top of each box
      const top = { x: p.x + p.w / 2, y: p.y }, vx = P.clients.x + P.clients.w + 18 + i * 10, hy = P.topEnd + 18 + (nS - 1 - i) * 11;
      S.svcPos[k] = p;
      S.svcLane[k] = edge('api', poly([o, { x: vx, y: o.y }, { x: vx, y: hy }, { x: top.x, y: hy }, top]), SVC_METHOD[k], 0.93, 'ln-' + k, `clients>${k}:api`);
      if (!gr || gedge(`faults>${k}:inj`)) lanes.append(sv('path', { class: 'lane inj', 'data-edge': `faults>${k}:inj`, 'data-plane': 'faults', 'data-measured': true, d: dpath(poly([injOut, { x: vx + 40, y: injOut.y }, { x: vx + 40, y: hy + 4 }, { x: top.x + 16, y: hy + 4 }, { x: top.x + 16, y: top.y }])), opacity: 0.3 }));
    }
  });
  if (!col) {
    for (const [l, p] of place) if (!Array.isArray(l)) {
      edge('jump', curve({ x: p.x + p.w, y: p.y + p.h / 2 - 8 }, { x: P.cluster.x, y: p.y + p.h / 2 - 8 }), 'jump host', 0.5, null, `${l.name}>nodes:jump`);
      edge('jump', curve({ x: p.x + p.w, y: p.y + p.h / 2 + 8 }, { x: P.cluster.x, y: p.y + p.h / 2 + 8 }), 'kubectl', 0.5, null, `${l.name}>k8s-nodes:kubectl`);
    }
    if (tpBox) edge('teleport', curve({ x: tpP.x + tpP.w, y: tpP.y + tpP.h / 2 }, { x: P.cluster.x, y: tpP.y + tpP.h / 2 }), 'Teleport', 0.5, null, 'teleport>nodes:teleport');
  }
  for (const [a2, p] of place) {
    if (a2 === tpBox) { g.append(box('teleport', tpBox[1], tpBox[2], p, 'svc', 'teleport', 'blkacc')); continue; }
    if (Array.isArray(a2)) { g.append(accBox(a2, p)); continue; }
    const l = a2, lt = () => !loginRuns(l) ? [`${l.name} · not running`, stackUp() ? 'Selected, but nothing answers on its port 22: build it with benchctl up' : 'The stack is not running'] : [`${l.name} · ${MODE_TEXT[l.mode]}`, l.mode === 'teleport' ? 'No direct port: only through the Teleport proxy' : `${l.ip || l.name} (also 127.0.0.1:${l.port}) · click for the command`];
    const bg = asButton(sv('g', { id: 'lg-' + l.name }), `${l.name}: ${MODE_TEXT[l.mode]}${loginRuns(l) ? '' : ', not running'}. Details`, () => openPop(bg, 'login', l.name), lt);
    const lines = col ? [`${l.name} · ${KIND_NAME[loginKind(l)]}`, MODE_TEXT[l.mode], loginRuns(l) ? (l.ip || l.name) : '✗ not running', loginRuns(l) ? portLine(l) : ''] : [`${l.name} · ${KIND_NAME[loginKind(l)]}`, MODE_TEXT[l.mode], loginRuns(l) ? loginAddr(l) : '✗ not running'];
    bg.append(sv('rect', { class: 'blk lgn' + (loginRuns(l) ? '' : stackUp() ? ' down' : ' off'), x: p.x, y: p.y, width: p.w, height: p.h, rx: 10 }), ...lines.map((t, n) => t ? [sv('text', { class: n ? 'sub' : 'sn', x: p.x + 12, y: p.y + mA.ys[n] }, fitText(t, n ? 'sub' : 'sn', u, p.w - 24, n ? 400 : 600))] : []).flat()); g.append(bg);
  }
  svcs.forEach(([k, label, sb], i) => g.append(box(k, label, sb, P.svcP[i], 'svc', k)));
  const chipW = P.chipW, chipFs = UF.chip * u;
  CHIPS.forEach(([t], i) => {
    const cg = asButton(sv('g', {}), `${t}: how it connects`, () => openPop(cg, 'client', t), () => [t, CLIENT_INFO[t]]), px = col ? P.clients.x + 12 + i * (chipW + 10) : 20, py = col ? cy + tb + 12 : cy + 48 + i * 60;
    cg.append(sv('rect', { class: 'chipbg', x: px, y: py, width: chipW, height: P.chipH, rx: 9 }), sv('text', { class: 'ndt cl', x: px + 10, y: py + P.chipH / 2 + chipFs * 0.35 }, t)); g.append(cg);
  });
  const tm = P.tileM;
  for (const item of P.tiles) {
    if (item[0] === 'label') { g.append(sv('text', { class: 'ftitle', x: P.cluster.x + 24, y: P.cluster.y + item[2] + 4 }, GROUP_TITLE[item[1]])); continue; }
    const [, n, rx, ry] = item, x = P.cluster.x + rx, ty = P.cluster.y + ry, glyph = STATE_ICON[n.state] || '?';
    const tg = asButton(sv('g', { id: 'nd-' + n.name }), `${n.name}: ${n.label}. Details`, () => openPop(tg, 'node', n.name), () => { const c = nodeList().find(m => m.name === n.name) || n; return [`${c.name} · ${c.label}`, 'click for how to reach it and its actions']; });
    const [temp, gpu] = tileFacts(n), cr = UF.tile * u * 0.8, ccx = x + P.tw - 8 - cr, ccy = ty + tm.y1 - UF.tile * u * 0.3;
    tg.append(sv('rect', { class: 'nd ' + n.state, x, y: ty, width: P.tw, height: P.th, rx: 7 }), sv('text', { class: 'ndt', x: x + 8, y: ty + tm.y1 }, n.name), sv('text', { class: 'ndg', x: ccx, y: ccy + UF.tile * u * 0.36, 'text-anchor': 'middle' }, glyph),
      sv('circle', { class: 'busy' + (n.gpus_busy > 0 ? ' on' : ''), cx: x + P.tw - 11, cy: ty + P.th - 11, r: 4.5 }), sv('text', { class: 'sub', x: x + 8, y: ty + tm.y2 }, temp), sv('circle', { class: 'ndc ' + n.state, cx: ccx, cy: ccy, r: cr }));
    if (P.th >= tm.y3 + 10) tg.append(sv('text', { class: 'sub', x: x + 8, y: ty + tm.y3 }, gpu));  // a tall tile has room for the GPU load
    g.append(tg); S.tiles[n.name] = { x, y: ty, w: P.tw, h: P.th, g: tg };
  }
  g.append(labels, sv('g', { id: 'dots' })); paintBlocks();
}
export const GROUP_TITLE = { compute: 'Slurm compute nodes', gpu: 'Slurm GPU nodes', k8s: 'Kubernetes nodes' };
export const tileFacts = n => n.kind === 'k8s' ? ['KWOK', n.gpus ? `${n.gpus} GPU` : 'CPU'] : [n.temp != null ? `${n.temp} °C` : '', n.gpus ? `${n.gpus_busy || 0}/${n.gpus} GPU` : ''];
export function paintTiles() {  // node states change without a rebuild; a different node list does rebuild
  const nodes = nodeList(), sig = flowSig(nodes);
  if (sig !== S.flowNodes || fitKey() !== S.flowFitKey) { buildFlow(); return; }
  if (curLevel() === 'overview') { refreshOverview(); return; }
  for (const n of nodes) { const t = S.tiles[n.name]; if (t) { t.g.setAttribute('aria-label', `${n.name}: ${n.label}. Details`); t.g.children[0].setAttribute('class', 'nd ' + n.state); t.g.children[2].textContent = STATE_ICON[n.state] || '?'; t.g.children[3].setAttribute('class', 'busy' + (n.gpus_busy > 0 ? ' on' : '')); const [fa, fb] = tileFacts(n); t.g.children[4].textContent = fa; if (t.g.children[6]) t.g.children[6].textContent = fb; t.g.children[5].setAttribute('class', 'ndc ' + n.state); } }
}
export function paintBlocks() {
  if (curLevel() === 'overview') { refreshOverview(); return; }
  if (!S.flowSnap || !S.svcPos['bmc-farm']) return;
  const rate = {}, cut = Date.now() - 60000;
  S.recent = S.recent.filter(r => r.t > cut); S.recent.forEach(r => { rate[r.to] = (rate[r.to] || 0) + r.n; });
  let sent = 0; const allOff = !Object.values(S.flowSnap.reachable).some(Boolean);
  for (const [k] of svcList()) {
    const up = S.flowSnap.reachable[k]; sent += S.flowSnap.totals[k] || 0;
    $('fb-' + k).setAttribute('class', 'blk ' + (up ? 'up' : allOff ? 'off' : 'down') + (k === 'teleport' ? '' : REAL_SERVICES.has(k) ? ' real' : ' sim'));  // everything off = stack not running: stay calm, not red
    const fitBox = (...alts) => { for (const t of alts) { if (fitText(t, 'sub', S.unitNow, svcFit[k] || 190) === t) return t; } return fitText(alts[alts.length - 1], 'sub', S.unitNow, svcFit[k] || 190); };  // the first wording that fits; shorter ones drop words, never end in an ellipsis
    $('fs-' + k).textContent = fitBox(up ? (k === 'jira-real' || k === 'k8s-api' || k === 'dcgm-exporter' || k === 'real-gpu' ? '✓ up · no counter' : '✓ up · ' + (rate[k] || 0) + (k === 'teleport' ? ' conn/min' : ' req/min')) : allOff ? '– offline' : '✗ not reachable');
    $('ln-' + k).setAttribute('class', 'lane' + (rate[k] ? ' on' : ''));
    const flt = S.recent.some(r => r.fault && r.to === k && r.t > Date.now() - 30000);  // a fault was applied to it in the last 30 s
    $('ff-' + k).setAttribute('class', 'fring' + (flt ? ' on' : '')); $('fx-' + k).setAttribute('class', 'badge flt' + (flt ? ' on' : '')); $('fa-' + k).setAttribute('class', 'badge act' + (rate[k] ? ' on' : ''));
    const si = document.getElementById('si-' + (k === 'gpu-exporter' ? 'gpu-exporter' : k)); if (si) si.textContent = k === 'jira-real' || k === 'k8s-api' || k === 'dcgm-exporter' || k === 'real-gpu' ? 'no request counter (the real service publishes none)' : `${rate[k] || 0} ${k === 'teleport' ? 'conn' : 'req'}/min · ${(S.flowSnap.totals[k] || 0).toLocaleString()} seen`;
    const ne = S.flowSnap.errors && S.flowSnap.errors[k]; $('fc-' + k).textContent = (S.flowSnap.totals[k] || 0) ? (ne ? fitBox(`${S.flowSnap.totals[k].toLocaleString()} ${S.flowSnap.totals[k] === 1 ? 'request' : 'requests'} · ${ne} ${ne === 1 ? 'error' : 'errors'}`, `${S.flowSnap.totals[k].toLocaleString()} · ${ne} ${ne === 1 ? 'error' : 'errors'}`, `${ne} ${ne === 1 ? 'error' : 'errors'}`) : fitBox(S.flowSnap.totals[k].toLocaleString() + (S.flowSnap.totals[k] === 1 ? ' request seen' : ' requests seen'), S.flowSnap.totals[k].toLocaleString() + ' requests', S.flowSnap.totals[k].toLocaleString())) : fitBox(svcList().find(x => x[0] === k)[2]);
  }
  $('fc-clients').textContent = sent.toLocaleString() + ' requests';
  $('fc-injector').textContent = (S.flowSnap.faults || 0) + (S.flowSnap.faults === 1 ? ' fault applied' : ' faults applied');
}
export function flowNote() {
  const why = S.flowPaused ? 'Animation paused by you.' : reduced.matches ? 'Animation off: your system asks for reduced motion; counts still update.'
    : S.flowLevel >= 3 ? `Animation paused: the governor is at level L${S.flowLevel} and needs the resources.` : 'Live. Dots appear when a service gets a request or a fault is applied.';
  $('flowstate').textContent = why;
}

export function init() {
  $('crumb-all').onclick = () => setLevel('overview', false);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && S.focusId && curLevel() === 'full' && !S.openNode && $('ckey').hidden && $('pop').hidden && !document.querySelector('dialog[open]:not(.drawer)')) { focusOn(null); return; }
    if (e.key === 'Escape' && S.dived && curLevel() === 'full' && !S.openNode && $('ckey').hidden && $('pop').hidden && !document.querySelector('dialog[open]:not(.drawer)')) setLevel('overview', false); });

}
