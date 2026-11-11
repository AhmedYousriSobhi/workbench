import { S } from './state.js';

export const $ = id => document.getElementById(id);
export const H = { 'X-Requested-With': 'workbench' };
export const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// live regions (role=status) are written only when their content changes, so a screen reader hears a change once, not every second (U3)
export function setText(el, text) { if (el.textContent !== text) el.textContent = text; }
export function setHTML(el, html) { if (el.dataset.html !== html) { el.dataset.html = html; el.innerHTML = html; } }
// Dynamic sizes travel as data attributes and are applied through the CSSOM, which a CSP without 'unsafe-inline' allows (#517).
export function paintDyn(root) {
  for (const e of root.querySelectorAll('[data-w],[data-flex],[data-p],[data-bg]')) {
    const d = e.dataset;
    if (d.w !== undefined) e.style.width = d.w + '%';
    if (d.flex !== undefined) e.style.flex = d.flex;
    if (d.p !== undefined) e.style.setProperty('--p', d.p + 's');
    if (d.bg !== undefined) e.style.background = d.bg;
  }
}
export const enc = encodeURIComponent;  // one path segment of an API URL (U8)
export function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.hidden = false; clearTimeout(S.flashTimer); S.flashTimer = setTimeout(() => { t.hidden = true; }, 6000);
}
export function announce(t) { let a = $('focuslive'); if (!a) { a = document.createElement('div'); a.id = 'focuslive'; a.className = 'srtable'; a.setAttribute('aria-live', 'polite'); document.body.append(a); } a.textContent = t; }
