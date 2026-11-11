import { SVGNS } from './flowstate.js';

export function sv(tag, attrs, text) { const e = document.createElementNS(SVGNS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; }
export const curve = (a, b) => { const dx = (b.x - a.x) * 0.5; return [a, { x: a.x + dx, y: a.y }, { x: b.x - dx, y: b.y }, b]; };
export const poly = pts => ({ poly: pts });  // straight segments (the service lines run along a channel); position by distance, like a curve by t
export const atPoly = (pts, t) => { const L = pts.slice(1).map((q, i) => Math.hypot(q.x - pts[i].x, q.y - pts[i].y)), tot = L.reduce((a, b) => a + b, 0); let d = Math.min(Math.max(t, 0), 1) * tot, i = 0; while (i < L.length - 1 && d > L[i]) d -= L[i++]; const k = L[i] ? d / L[i] : 0; return { x: pts[i].x + (pts[i + 1].x - pts[i].x) * k, y: pts[i].y + (pts[i + 1].y - pts[i].y) * k }; };
export const at = (b, t) => { if (b.poly) return atPoly(b.poly, t); const [p0, p1, p2, p3] = b; const u = 1 - t, f = (c) => u ** 3 * p0[c] + 3 * u * u * t * p1[c] + 3 * u * t * t * p2[c] + t ** 3 * p3[c]; return { x: f('x'), y: f('y') }; };
export function roundedPoly(pts, r = 16) {  // straight segments joined by small arcs, like a wiring diagram
  let d = `M${pts[0].x} ${pts[0].y}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const a = pts[i - 1], b = pts[i], c = pts[i + 1], l1 = Math.hypot(b.x - a.x, b.y - a.y), l2 = Math.hypot(c.x - b.x, c.y - b.y);
    if (!l1 || !l2) { d += ` L${b.x} ${b.y}`; continue; }
    const k = Math.min(r, l1 / 2, l2 / 2);
    d += ` L${b.x + (a.x - b.x) * k / l1} ${b.y + (a.y - b.y) * k / l1} Q${b.x} ${b.y} ${b.x + (c.x - b.x) * k / l2} ${b.y + (c.y - b.y) * k / l2}`;
  }
  const z = pts[pts.length - 1]; return d + ` L${z.x} ${z.y}`;
}
export const dpath = b => b.poly ? roundedPoly(b.poly) : `M${b[0].x} ${b[0].y} C${b[1].x} ${b[1].y} ${b[2].x} ${b[2].y} ${b[3].x} ${b[3].y}`;
export const kindOf = n => n.kind === 'real-gpu' ? 'gpu' : n.kind;  // the real card's node sits with the GPU nodes (issue 428)
