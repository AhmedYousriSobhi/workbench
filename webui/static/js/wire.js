// The shared wire (redesign step 2, docs/design/ui-redesign/DESIGN_HANDOFF.md, "Visual language").
//
// A live wire is four stacked SVG strokes: a dark base, a plane-coloured halo, a thin core and a pulse of moving dots.
// Errors add a red dash on top. A wire that carries nothing is one dashed grey stroke ("no current"). All colour and motion
// live in canvas.css; this file decides which strokes a wire has and how fast the pulse moves.
//
//   const g = buildWire({ d: 'M0 8 L79 8', plane: 'access', rpm: 12, errors: 0, label: 'Login to slurmctld' });
//   svg.append(g);
//   updateWire(g, { d, plane, rpm: 60, errors: 2, label });   // same node, new rate: nothing is rebuilt
import { sv } from './svg.js';

export const PLANES = ['access', 'control', 'k8s', 'data', 'monitoring', 'management'];
export const PLANE_NAME = { access: 'Access', control: 'Control', k8s: 'Kubernetes control', data: 'Data', monitoring: 'Monitoring', management: 'Management' };

// Dash periods (dash + gap, in user units). canvas.css animates each stroke by exactly its own period, so the loop is
// seamless; a period that does not divide the keyframe distance makes the dots jump when the animation restarts.
export const PULSE_PERIOD = 20;  // dasharray "2 18"
export const ERR_PERIOD = 61;    // dasharray "1 60"

export const SLOWEST = 4, FASTEST = 0.45;  // seconds for the pulse to travel one period

// Seconds the pulse takes to travel one period: more requests per minute, faster dots, never faster than FASTEST (a blur)
// or slower than SLOWEST (it stops reading as motion). null means no current: nothing moves. Rate is a log scale because
// the interesting range is 1 to 1000 requests a minute.
export function pulseSeconds(rpm) {
  if (!(rpm > 0)) return null;
  return Math.min(SLOWEST, Math.max(FASTEST, SLOWEST / (1 + Math.log2(1 + rpm / 6))));
}

// The same ground speed for the longer error dash: seconds for it to travel one of its own periods.
export const errSeconds = pulse => pulse * ERR_PERIOD / PULSE_PERIOD;

// What a wire says in words, for screen readers and the tooltip: the picture is never the only carrier of the state.
export function wireLabel({ plane, rpm = 0, errors = 0, label = '', declared = false }) {
  const rate = rpm > 0 ? `${Math.round(rpm * 10) / 10} requests a minute` : declared ? 'declared wiring, not measured' : 'no current';
  const err = errors > 0 ? `, ${errors} ${errors === 1 ? 'error' : 'errors'}` : '';
  return `${label ? label + ': ' : ''}${PLANE_NAME[plane] ?? plane} wire, ${rate}${err}`;
}

// The strokes of a wire, bottom to top, as data: [{ cls, d, style? }]. A wire with no current is the dark base and one
// dashed core; a live one adds halo and pulse; errors add the red dash. `style` holds CSS variables for canvas.css.
export function wireLayers({ d, rpm = 0, errors = 0, declared = false }) {
  const pulse = pulseSeconds(rpm);
  if (pulse === null && declared) return [{ cls: 'wire-base', d }, { cls: 'wire-halo', d }, { cls: 'wire-core', d }];  // how it is wired, with nothing counting it: lit, but nothing moves
  if (pulse === null) return [{ cls: 'wire-base', d }, { cls: 'wire-idle', d }];
  const layers = [{ cls: 'wire-base', d }, { cls: 'wire-halo', d }, { cls: 'wire-core', d }, { cls: 'wire-pulse', d, style: { '--dur': `${pulse.toFixed(2)}s` } }];
  if (errors > 0) layers.push({ cls: 'wire-err', d, style: { '--dur': `${errSeconds(pulse).toFixed(2)}s` } });
  return layers;
}

function paint(g, o) {
  const plane = PLANES.includes(o.plane) ? o.plane : 'control';
  g.setAttribute('data-plane', plane);
  g.setAttribute('data-state', !(o.rpm > 0) ? (o.declared ? 'declared' : 'idle') : o.errors > 0 ? 'errors' : 'live');
  const layers = wireLayers(o);
  g.replaceChildren(sv('title', {}, wireLabel({ ...o, plane })), ...layers.map(l => {
    const p = sv('path', { class: l.cls, d: l.d });
    for (const k in l.style || {}) p.style.setProperty(k, l.style[k]);  // CSSOM, not a style attribute: allowed under a strict CSP
    return p;
  }));
}

// A wire as an SVG group. The group has role="img" and a title, so it is announced as text ("Access wire, 12 requests a
// minute, 2 errors") and a hover shows the same words.
export function buildWire(o) {
  return updateWire(sv('g', { class: 'wire', role: 'img' }), o);
}

// Change a wire in place. Only touches the DOM when something that shows has changed (the signature below), so a poll
// that returns the same numbers costs nothing and an animation is not restarted.
export function updateWire(g, o) {
  const sig = [o.d, o.plane, wireLayers(o).map(l => l.cls + (l.style?.['--dur'] ?? '')).join(), wireLabel(o)].join('|');
  if (g.dataset.sig === sig) return g;
  g.dataset.sig = sig;
  paint(g, o);
  return g;
}

// A part that needs attention breathes amber and always says why in words (never colour or motion alone).
export function markAttention(el, text) {
  el.classList.add('breathe');
  let t = el.querySelector(':scope > .attn-text');
  if (!t) { t = document.createElement('span'); t.className = 'attn-text'; el.append(t); }
  t.textContent = `▲ ${text}`;
  return el;
}
export function clearAttention(el) { el.classList.remove('breathe'); el.querySelector(':scope > .attn-text')?.remove(); return el; }

// Every wire animation stops while the tab is hidden (W-11): canvas.css pauses them when <html> has data-tab-hidden.
// Reduced motion is handled in the stylesheet by a media query, so it needs no code here.
export function init() {
  const sync = () => document.documentElement.toggleAttribute('data-tab-hidden', document.hidden);
  document.addEventListener('visibilitychange', sync);
  sync();
}
