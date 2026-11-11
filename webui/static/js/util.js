import { S } from './state.js';

export const $ = id => document.getElementById(id);
export const H = { 'X-Requested-With': 'workbench' };
export const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
export function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.hidden = false; clearTimeout(S.flashTimer); S.flashTimer = setTimeout(() => { t.hidden = true; }, 6000);
}
export function announce(t) { let a = $('focuslive'); if (!a) { a = document.createElement('div'); a.id = 'focuslive'; a.className = 'srtable'; a.setAttribute('aria-live', 'polite'); document.body.append(a); } a.textContent = t; }
