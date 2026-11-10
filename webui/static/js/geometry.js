import { S } from './state.js';
import { accExtra, accUp, hasTeleport, scoped } from './flowstate.js';
import { $ } from './util.js';
import { dockWide } from './inspector.js';
import { curLevel } from './overview.js';
import { kindOf } from './svg.js';

export function flowSig(nodes) { return nodes.map(n => n.name).join(',') + (hasTeleport() ? '+teleport' : '') + accExtra().map(b => '+' + b[0] + (accUp(b[0]) ? '' : ':down')).join('') + '|' + loginList().map(l => l.name + ':' + l.mode + ':' + loginKind(l) + (l.up === false ? ':down' : '')).join(','); }
// ---- the diagram (#192): clients -> services and access (login nodes, Teleport) -> nodes, one labelled line per way in ----
export const SVC_ICON = { 'bmc-farm': 'M4 5h16v5H4z M4 14h16v5H4z M7 7.5h.01 M7 16.5h.01', 'bcm-mock': 'M3 5h18v14H3z M3 9h18', 'jira-mock': 'M12 3l9 9-9 9-9-9z', 'jira-real': 'M12 3l9 9-9 9-9-9z M12 8l4 4-4 4-4-4z',
  'k8s-api': 'M12 3l8 4.5v9L12 21l-8-4.5v-9z M12 8v8 M8.5 10l7 4 M15.5 10l-7 4',
  'real-gpu': 'M7 7h10v10H7z M9.5 12l2 2 3.5-4 M10 3v4 M14 3v4 M10 17v4 M14 17v4 M3 10h4 M3 14h4 M17 10h4 M17 14h4',
  'dcgm-exporter': 'M7 7h10v10H7z M10 12l2 2 3-4 M10 3v4 M14 3v4 M10 17v4 M14 17v4',
  'gpu-exporter': 'M7 7h10v10H7z M10 3v4 M14 3v4 M10 17v4 M14 17v4 M3 10h4 M3 14h4 M17 10h4 M17 14h4', teleport: 'M12 3l8 3v6c0 5-4 8-8 9-4-1-8-4-8-9V6z' };  // decorative glyphs, one per kind
export const SVC_METHOD = { 'bmc-farm': 'HTTPS Redfish', 'bcm-mock': 'HTTP JSON', 'jira-mock': 'HTTP REST', 'jira-real': 'HTTP REST', 'k8s-api': 'HTTPS kubectl', 'gpu-exporter': 'HTTP metrics', 'dcgm-exporter': 'HTTP metrics', 'real-gpu': 'HTTP metrics', teleport: 'tsh · HTTPS' };
export const MODE_TEXT = { ssh: 'Direct SSH', teleport: 'Teleport only', both: 'Direct SSH + Teleport' };
export const KIND_TEXT = { slurm: 's-login · Slurm', k8s: 'k-login · Kubernetes', both: 'login · Slurm + Kubernetes' },
  KIND_NAME = { slurm: 's-login', k8s: 'k-login', both: 's+k login' };
export const loginKind = l => l.kind || 'both';
export const LOGIN_ONE = [{ name: 'login', mode: 'ssh', kind: 'both', port: 2222 }];
export const stackUp = () => { try { return Object.values(JSON.parse(S.lastCluster).services).some(Boolean); } catch (e) { return false; } };
export const loginRuns = l => l.up !== false;  // nothing answers on its port 22: it was selected, but it is not built or not running
export const loginAddr = l => (l.ip || l.name) + (l.mode === 'teleport' ? ' · no port' : ` · :${l.port}`);  // the address a client dials (private IP; the published port is on 127.0.0.1)
export function loginList() { try { const l = S.lastCluster && scoped().logins; return l && l.length ? l : LOGIN_ONE; } catch (e) { return LOGIN_ONE; } }
export const GAP = 14;
// Text has one size on the screen, whatever the scale of the drawing (#319): UF is in screen pixels, `u` is drawing units per pixel
// (the inverse of the scale), set as --u on the SVG. Every box is sized from its measured text, so nothing spills out of a box.
export const UF = { sn: 14, sub: 11.5, title: 11, tile: 11, chip: 13, lab: 11.5 },
  FAM = 'system-ui, sans-serif';
export const measureCtx = document.createElement('canvas').getContext('2d');
export function textW(t, k, u, weight = 400, upper = false) {  // the width of a text in drawing units
  const str = upper ? String(t).toUpperCase() : String(t); measureCtx.font = `${weight} ${UF[k] * u}px ${FAM}`;
  return measureCtx.measureText(str).width + (upper ? str.length * UF[k] * u * 0.05 : 0);
}
export function fitText(text, k, u, maxW, weight = 400) {  // safety net for a name or number longer than any sample: cut with an ellipsis
  if (textW(text, k, u, weight) <= maxW) return text;
  let lo = 0, hi = text.length; while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (textW(text.slice(0, mid) + '…', k, u, weight) <= maxW) lo = mid; else hi = mid - 1; }
  return text.slice(0, lo).trimEnd() + '…';
}
export const LEGACY = { ys: [24, 42, 58, 74], h: 68 };  // the narrow-screen drawing keeps its fixed box
export function boxM(u, lines) {  // baselines and height of a box with a bold name and `lines - 1` detail lines
  const sn = UF.sn * u, sub = UF.sub * u, ys = [8 + sn * 0.9];
  for (let n = 1; n < lines; n++) ys.push(ys[n - 1] + sub * (n === 1 ? 1.4 : 1.3));
  return { ys, h: Math.ceil(ys[lines - 1] + 12) };
}
export const portLine = l => l.mode === 'teleport' ? 'no direct port' : `port ${l.port}`;
export function flowFit() {  // the shape of the panel the diagram must fill; narrow screens keep the natural 1180-wide drawing
  if (!dockWide()) return { a: 0, col: false };
  const r = $('flow').getBoundingClientRect(), a = r.width > 80 && r.height > 80 ? r.width / r.height : 0;
  return { a, col: a > 0 };
}
export const fitKey = () => { const f = flowFit(); return (curLevel() === 'overview' ? 'o' : '') + (f.a ? 'c' + Math.round(f.a * 6) : 'n'); };
export function tileGrid(nodes, cols, tw, th, top = 52) {  // node tiles by group, relative to the cluster frame: [label|node, ...], and the height the frame needs
  let ry = top; const items = [];
  for (const k of ['compute', 'gpu', 'k8s']) {
    const rows = nodes.filter(n => kindOf(n) === k); if (!rows.length) continue;
    items.push(['label', k, ry]); ry += 26;
    rows.forEach((n, i) => items.push(['node', n, 24 + (i % cols) * (tw + GAP), ry + Math.floor(i / cols) * (th + GAP)]));
    ry += Math.ceil(rows.length / cols) * (th + GAP) + 22;
  }
  return { items, h: ry + 4 };
}
export function flowGeom(nodes, acc, svcs, a, col, u) {
  const M = 8, nS = svcs.length, P = { col, u, tb: 26 }, W = (t, k, w) => textW(t, k, u, w);
  const tn = UF.tile * u, tw0 = Math.max(64, Math.ceil(Math.max(0, ...nodes.map(n => W(n.name, 'tile', 600))) + 16 + tn * 1.7));  // room for the name and the status circle
  const th0 = Math.ceil(10 + tn * 0.9 + tn * 1.3 + 10), thFacts = Math.ceil(th0 + tn * 1.35), twFacts = Math.ceil(W('8/8 GPU', 'sub') + 16);
  P.tileM = { y1: 8 + tn * 0.9, y2: 8 + tn * 0.9 + tn * 1.3, y3: 8 + tn * 0.9 + tn * 2.6 };
  if (col) {  // top to bottom: Clients row, Access row, Cluster nodes; the Services column sits at the right and its lines run down a gutter and enter from the left
    const tb = 10 + UF.title * u * 0.95, chipH = Math.ceil(UF.chip * u * 1.3 + 22), R1 = Math.ceil(tb + 12 + chipH + 14), GAPY = 64, GW = 110;
    const mA = boxM(u, 4), mS = boxM(u, 3), nT = hasTeleport() ? acc.filter(l => l.mode && l.mode !== 'ssh').length : 0, nKey = acc.filter(x => !Array.isArray(x) && x.mode !== 'teleport').length, hasJ = acc.some(x => Array.isArray(x) && x[0] === 'jump'), hasC = acc.some(x => Array.isArray(x) && x[0] === 'controller'), nU = nT + (hasJ ? nKey + (hasC ? 1 : 0) : 0) + (hasC ? nKey : 0), ACCH = 46 + mA.h + 14 + (nU ? 20 + nU * 26 : 0), top = Math.ceil(tb + 34);
    const needA = Math.max(150, ...acc.map(x => Array.isArray(x) ? Math.max(W(x[1], 'sn', 600) + 44, W('✓ up · 99 conn/min', 'sub'), W('99,999 requests seen', 'sub')) + 24
      : Math.max(W(`${x.name} · ${KIND_NAME[loginKind(x)]}`, 'sn', 600), W(MODE_TEXT[x.mode], 'sub'), W(x.ip || x.name, 'sub'), W(portLine(x), 'sub')) + 24));
    const SWD = Math.ceil(Math.max(180, ...svcs.map(([, label, sub]) => Math.max(W(label, 'sn', 600) + 44, W('✓ up · 99,999 req/min', 'sub'), W('99,999 requests seen', 'sub'), W(sub, 'sub')) + 24))), svW = SWD + 32;
    const chipW = Math.ceil(Math.max(120, ...['Monitoring app', 'Prometheus', 'curl · scripts', 'You: ssh · tsh'].map(t => W(t, 'chip', 600) + 24)));
    let Wm = Math.max(4 * chipW + 54, acc.length * (Math.ceil(needA) + 14) + 18, 444);
    const make = (w, ex) => {
      const g1 = GAPY + ex * 0.25, g2 = GAPY + ex * 0.25, pitch = mS.h + 10 + (ex * 0.75) / Math.max(nS, 1);
      const y2 = M + R1 + g1, svH = 46 + nS * pitch + 8, y3 = y2 + ACCH + g2;
      let tw = tw0, th = th0, cols = Math.max(3, Math.floor((w - 48 + GAP) / (tw + GAP))), tg = tileGrid(nodes, cols, tw, th, top);
      const free = Math.max(y2 + svH - y3, tg.h);  // the tiles grow (up to 2x) into the height the Services column leaves, and then show temperature and GPU load
      for (let t = 1.1; t <= 2; t += 0.1) { const w2 = Math.max(Math.round(tw0 * t), twFacts), h2 = Math.max(Math.round(th0 * t), thFacts), c2 = Math.max(3, Math.floor((w - 48 + GAP) / (w2 + GAP))), g = tileGrid(nodes, c2, w2, h2, top); if (g.h > free) break; tw = w2; th = h2; cols = c2; tg = g; }
      const bottom = Math.max(y2 + svH, y3 + tg.h);
      return { cols, tg, tw, th, pitch, y2, y3, svH, bottom, W: M + w + GW + svW + M, H: bottom + M };
    };
    let L = make(Wm, 0);
    if (a && L.H * a > L.W) { Wm += L.H * a - L.W; L = make(Wm, 0); }  // the panel is wider than the drawing: widen the main column (more tile columns, wider boxes)
    if (a && L.W / L.H > a) L = make(Wm, L.W / a - L.H);               // the panel is taller: spread the extra height over the gaps and the service pitch
    const n = Math.max(acc.length, 1), bw = Math.min(Math.max(280, Math.ceil(needA)), (Wm - 32 - (n - 1) * 14) / n), x0 = M + (Wm - (n * bw + (n - 1) * 14)) / 2;
    Object.assign(P, { tb, chipH, chipW: Math.min(chipW + 40, (Wm - 24 - 30) / 4), mA, mS });
    P.clients = { x: M, y: M, w: Wm, h: R1 }; P.injector = { x: M + Wm + GW, y: M, w: svW, h: R1 };
    P.access = { x: M, y: L.y2, w: Wm, h: ACCH }; P.cluster = { x: M, y: L.y3, w: Wm, h: L.bottom - L.y3 }; P.svcFrame = { x: M + Wm + GW, y: L.y2, w: svW, h: L.svH };
    P.place = acc.map((_, i) => ({ x: x0 + i * (bw + 14), y: L.y2 + 46, w: bw, h: mA.h }));
    P.svcP = Array.from({ length: nS }, (_, i) => ({ x: P.svcFrame.x + 16, y: L.y2 + 46 + i * L.pitch, w: SWD, h: mS.h }));
    Object.assign(P, { tiles: L.tg.items, tw: L.tw, th: L.th, FW: Math.round(L.W), H: Math.round(L.bottom + M), gutter: { x0: M + Wm, x1: M + Wm + GW } });
    return P;
  }
  const BH = 68, PIT = 92, chipsH = 316, AW = 336;  // narrow screens: Clients and Faults | Access over Services | Cluster nodes
  const natural = 1180, cwide = 504, cols = Math.max(3, Math.floor((cwide - 48 + GAP) / (tw0 + GAP))), tg = tileGrid(nodes, cols, tw0, th0);
  const accH = 56 + acc.length * PIT, topH = Math.max(accH, tg.h), AX = 252, cx = 668;
  const svTop = 8 + topH + 84, svH = 46 + BH + 40, rightEdge = cx + cwide, H = Math.max(8 + chipsH + 20 + 124 + 20, svTop + svH + 20);
  const SPITCH = Math.min(222, Math.floor((rightEdge - AX - 36) / Math.max(nS, 1)));
  Object.assign(P, { chipH: 46, chipW: 140, mA: LEGACY, mS: LEGACY });
  P.clients = { x: 8, y: 8, w: 164, h: chipsH }; P.injector = { x: 8, y: 8 + chipsH + 20, w: 164, h: 124 };
  P.access = { x: AX, y: 8, w: AW, h: topH }; P.cluster = { x: cx, y: 8, w: cwide, h: topH }; P.svcFrame = { x: AX, y: svTop, w: rightEdge - AX, h: svH };
  P.place = acc.map((_, i) => ({ x: AX + 114, y: 8 + 56 + i * PIT, w: 216, h: BH }));
  P.svcP = Array.from({ length: nS }, (_, i) => ({ x: AX + 18 + i * SPITCH, y: svTop + 46, w: SPITCH - 26, h: BH }));
  Object.assign(P, { tiles: tg.items, tw: tw0, th: th0, FW: Math.round(Math.max(natural, rightEdge + 8)), H, topEnd: 8 + topH });
  return P;
}
