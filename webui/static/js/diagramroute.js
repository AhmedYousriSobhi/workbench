// Where the architecture diagram's lines end and where their labels sit (issue 388). Pure numbers in, numbers out
// (webui/tests/js/diagramroute.test.mjs); diagram.js draws. No DOM, no state.

export const SLURM_GROUPS = ['compute', 'gpu'];

// The fraction of a straight vertical line from y0 to y1 at which a label centred on yWant sits; kept off the two ends.
export function labelFrac(y0, y1, yWant) {
  const len = y1 - y0; if (!len) return 0.5;
  return Math.min(0.92, Math.max(0.08, (yWant - y0) / len));
}

// Where a polyline's total length is at fraction f of its segment i (a label placed on one run of a bent line).
export function polyFrac(pts, i, f) {
  const L = pts.slice(1).map((q, k) => Math.hypot(q.x - pts[k].x, q.y - pts[k].y)), tot = L.reduce((a, b) => a + b, 0);
  return tot ? (L.slice(0, i).reduce((a, b) => a + b, 0) + L[i] * f) / tot : 0.5;
}

// The row each node group starts on, from tileGrid()'s items (['label', kind, y] relative to the frame): the Slurm group
// (compute and GPU nodes, whichever comes first) and the Kubernetes group. `rule` is the y of the rule over the title.
// The rule sits just above the title text, whose height is the title's screen size times `u` (drawing units per pixel); lines end on it.
export const ruleDrop = (u = 1, titlePx = 11) => -(0.8 * titlePx * u + 2);
export function groupRows(items, frameY, u = 1) {
  const out = {};
  for (const it of items) {
    if (it[0] !== 'label') continue;
    const g = SLURM_GROUPS.includes(it[1]) ? 'slurm' : it[1] === 'k8s' ? 'k8s' : null;
    if (g && !out[g]) out[g] = { title: frameY + it[2] + 4, rule: frameY + it[2] + ruleDrop(u), label: it[1] };
  }
  return out;
}

// The vertical channels between tile columns: a line can run down one without touching a tile. x is absolute.
export function channelXs(frameX, cols, tw, gap, left = 24) {
  return Array.from({ length: Math.max(cols, 1) }, (_, c) => frameX + left + (c + 1) * (tw + gap) - gap / 2);
}

// Candidate x positions every `step` units over [lo, hi]: where a line may come down when nothing else limits it.
export function sweepXs(lo, hi, step = 4) {
  const out = []; for (let x = lo; x <= hi; x += step) out.push(x); return out;
}

// The candidate nearest `want` inside [lo, hi] that is not within `clear` of an x in `avoid` or `used` and is right of `minX`
// (clear of a title); null when there is none, so the caller chooses what to give up (a jog, or the nearest free x).
export function pickChannel(chs, want, { lo = -Infinity, hi = Infinity, avoid = [], used = [], minX = -Infinity, clear = 9 } = {}) {
  const ok = x => x >= lo && x <= hi && x >= minX && ![...avoid, ...used].some(a => Math.abs(a - x) < clear);
  return chs.filter(ok).reduce((a, x) => (a == null || Math.abs(x - want) < Math.abs(a - want) ? x : a), null);
}
