import { S } from './state.js';
import { $, H, esc, toast } from './util.js';

export async function connections() {
  if (S.connCache) return S.connCache;
  try { S.connCache = (await (await fetch('/api/connections')).json()).entities; } catch (e) { return []; }
  return S.connCache;
}
export function actionHtml(a) {  // link and uri open from a registry entry built on the host (never from user input); copy puts a command on the clipboard
  if (a.kind === 'post') return `<button class="btn small post" data-post="${esc(a.value)}">${esc(a.label)}</button>`;
  if (a.kind === 'copy') return `<button class="btn small copy" data-copy="${esc(a.value)}" aria-label="${esc(a.label)}">${esc(a.label)}</button>`;
  const ext = a.kind === 'link' ? ' target="_blank" rel="noopener noreferrer"' : '';
  return `<a class="btn small" href="${esc(a.value)}"${ext}>${esc(a.label)}${a.kind === 'link' ? ' ↗' : ''}</a>`;
}
export function bindPost() {  // a button that asks the governor through the page backend; one message back (the governor restarts, the page does not)
  document.querySelectorAll('.post').forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      const r = await fetch(b.dataset.post, { method: 'POST', headers: H }), j = await r.json();
      toast(r.ok ? j.message : '✗ ' + j.detail);
    } catch (e) { toast('✗ Could not reach the page backend.'); }
    setTimeout(() => { b.disabled = false; }, 4000);
  });
}
export function bindCopy() {  // Copy buttons: the label comes back after the click, and the toast announces it (aria-live)
  document.querySelectorAll('.copy').forEach(b => b.onclick = async () => {
    const label = b.dataset.label || (b.dataset.label = b.textContent);
    try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = 'Copied'; toast('Copied: ' + b.getAttribute('aria-label')); } catch (e) { b.textContent = 'Select and copy'; }
    setTimeout(() => { b.textContent = label; }, 1500);
  });
}
export async function paintConnections() {
  const rows = await connections();
  $('conn-rows').innerHTML = rows.map(e => `<tr data-id="${esc(e.id)}"><td data-h="Part"><b>${esc(e.name)}</b><div class="small dim">${esc(e.how).replace(/`([^`]+)`/g, '<code>$1</code>')}</div>${e.status ? `<div class="small" role="status">${esc(e.status)}</div>` : ''}</td>
    <td data-h="Address"><code>${esc(e.address)}</code></td><td data-h="Try it"><code>${esc(e.command)}</code><button class="btn small copy" data-copy="${esc(e.command)}" aria-label="Copy the command for ${esc(e.name)}">Copy</button></td>
    <td data-h="Credential">${e.credential ? `<code>${esc(e.credential)}</code>` : '<span class="dim">none</span>'}</td>
    <td data-h="Open">${(e.actions || []).map(actionHtml).join(' ') || '<span class="dim">none</span>'}</td></tr>`).join('') || '<tr><td colspan="5">Could not load.</td></tr>';
  bindCopy(); bindPost();
}
