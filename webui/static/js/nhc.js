import { S } from './state.js';
import { $, H, esc } from './util.js';

export const nhcMsg = (t) => { $('nhc-msg').textContent = t; };
export async function nhcCall(method, body) {
  const r = await fetch('/api/nhc', method === 'GET' ? {} : { method, headers: { ...H, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { nhcPaint(true); nhcMsg('✗ Refused, nothing changed: ' + (j.detail || r.status)); return false; }  // redraw from the last good state so a rejected edit does not linger
  S.nhcState = j; nhcPaint(); return true;
}
export function nhcPaint(keepMsg) {
  const list = $('nhc-list');
  list.innerHTML = S.nhcState.checks.map((c, i) => `<li class="nhcrow${c.enabled ? '' : ' off'}" data-name="${esc(c.name)}" draggable="true">
    <button type="button" class="nhcgrip" aria-hidden="true" tabindex="-1" title="Drag to reorder">⠿</button>
    <label class="tog"><input type="checkbox" data-act="toggle"${c.enabled ? ' checked' : ''} aria-label="${esc(c.name)} enabled"><span><span class="nhcname">${esc(c.name)}</span> <small class="dim">${c.enabled ? 'on' : 'off'}</small></span></label>
    <span class="nhcbtns"><button class="btn small" data-act="up" aria-label="Move ${esc(c.name)} up"${i === 0 ? ' disabled' : ''}>↑</button><button class="btn small" data-act="down" aria-label="Move ${esc(c.name)} down"${i === S.nhcState.checks.length - 1 ? ' disabled' : ''}>↓</button><button class="btn small" data-act="remove" aria-label="Remove ${esc(c.name)}">✕</button></span>
    <span class="nhcdesc small dim">${esc(c.description || '')}</span>
    ${c.declared.length ? `<span class="nhcparams">${c.declared.map(k => `<label>${esc(k)}<input data-param="${esc(k)}" value="${esc(c.params[k] || '')}" aria-label="${esc(c.name)} ${esc(k)}"></label>`).join('')}</span>` : ''}</li>`).join('')
    || '<li class="dim small">No checks: every node counts as healthy.</li>';
  $('nhc-pal').innerHTML = S.nhcState.templates.map(t => `<button type="button" class="nhcchip" draggable="true" data-tpl="${esc(t.name)}" title="${esc(t.description)}">✚ ${esc(t.name)}</button>`).join('');
  if (!keepMsg) nhcMsg(S.nhcState.problems.length ? '✗ ' + S.nhcState.problems[0] : '');
}
export function nhcOrderAfterMove(name, to) {
  const names = S.nhcState.checks.map(c => c.name).filter(n => n !== name); names.splice(Math.max(0, Math.min(to, names.length)), 0, name); return names;
}
export function nhcDropIndex(y) {  // the slot under the pointer: before the first row whose middle is below it
  const rows = [...$('nhc-list').querySelectorAll('.nhcrow:not(.dragging)')]; let i = rows.findIndex(r => { const b = r.getBoundingClientRect(); return y < b.top + b.height / 2; });
  return { index: i < 0 ? rows.length : i, rows };
}
export function nhcMark(y) {
  document.querySelectorAll('.nhcrow.over').forEach(r => r.classList.remove('over')); const { index, rows } = nhcDropIndex(y);
  $('nhc-list').classList.toggle('drop-end', index === rows.length); if (rows[index]) rows[index].classList.add('over'); return index;
}
export function nhcEndDrag() { S.nhcDrag = null; document.querySelectorAll('.nhcrow').forEach(r => r.classList.remove('dragging', 'over')); $('nhc-list').classList.remove('drop-end'); }
export function nhcAskNew(tpl, position) {  // a template needs a name and its parameters before it can be validated
  const t = S.nhcState.templates.find(x => x.name === tpl); S.nhcPending = { tpl, position };
  $('nhc-new-params').innerHTML = t.declared.map(k => `<label>${esc(k)}<input data-newparam="${esc(k)}" required></label>`).join('');
  $('nhc-new').hidden = false; $('nhc-new-name').value = ''; $('nhc-new-name').focus(); nhcMsg(`New check from ${tpl}: name it and fill in its parameters.`);
}
export async function openNhc() { $('nhc-new').hidden = true; try { const r = await fetch('/api/nhc'); S.nhcState = await r.json(); nhcPaint(); } catch (e) { nhcMsg('✗ Could not load the checks.'); } $('nhcdlg').showModal(); }

export function init() {
  $('nhc-list').addEventListener('click', e => {
    const b = e.target.closest('[data-act]'); if (!b) return;
    const name = b.closest('.nhcrow').dataset.name, i = S.nhcState.checks.findIndex(c => c.name === name);
    const move = (to, word, act) => nhcCall('POST', { op: 'reorder', order: nhcOrderAfterMove(name, to) }).then(ok => {
      if (!ok) return; nhcMsg(`✓ ${name} moved ${word}`);  // the list was redrawn: keep the keyboard on the same row
      const row = [...document.querySelectorAll('.nhcrow')].find(r => r.dataset.name === name); const btn = row && (row.querySelector(`[data-act=${act}]:not(:disabled)`) || row.querySelector('[data-act=up]:not(:disabled),[data-act=down]:not(:disabled)')); if (btn) btn.focus();
    });
    if (b.dataset.act === 'up') move(i - 1, 'up', 'up');
    if (b.dataset.act === 'down') move(i + 1, 'down', 'down');
    if (b.dataset.act === 'remove' && confirm(`Remove check ${name}? Its file is deleted (git keeps the old one). Switch it off instead to keep it.`)) nhcCall('POST', { op: 'remove', name }).then(ok => ok && nhcMsg(`✓ removed ${name}`));
  });
  $('nhc-list').addEventListener('change', e => {
    const row = e.target.closest('.nhcrow'); if (!row) return; const name = row.dataset.name;
    if (e.target.dataset.act === 'toggle') nhcCall('POST', { op: 'toggle', name, enabled: e.target.checked }).then(ok => { if (ok) nhcMsg(`✓ ${name} ${e.target.checked ? 'on' : 'off'}`); });
    if (e.target.dataset.param) { const params = {}; row.querySelectorAll('[data-param]').forEach(i => params[i.dataset.param] = i.value);
      nhcCall('POST', { op: 'params', name, params }).then(ok => { if (ok) nhcMsg(`✓ ${name} saved`); }); }
  });
  document.addEventListener('dragstart', e => {
    const row = e.target.closest && e.target.closest('.nhcrow'), chip = e.target.closest && e.target.closest('.nhcchip');
    if (row) { S.nhcDrag = { name: row.dataset.name }; row.classList.add('dragging'); } else if (chip) S.nhcDrag = { tpl: chip.dataset.tpl }; else return;
    e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', S.nhcDrag.name || S.nhcDrag.tpl);
  });
  $('nhc-list').addEventListener('dragover', e => { if (S.nhcDrag) { e.preventDefault(); nhcMark(e.clientY); } });
  $('nhc-list').addEventListener('drop', e => {
    if (!S.nhcDrag) return; e.preventDefault(); const index = nhcMark(e.clientY), d = S.nhcDrag; nhcEndDrag();
    if (d.name) nhcCall('POST', { op: 'reorder', order: nhcOrderAfterMove(d.name, index) }).then(ok => ok && nhcMsg(`✓ ${d.name} moved`));
    else nhcAskNew(d.tpl, index + 1);
  });
  document.addEventListener('dragend', nhcEndDrag);
  $('nhc-pal').addEventListener('click', e => { const c = e.target.closest('.nhcchip'); if (c) nhcAskNew(c.dataset.tpl, null); });
  $('nhc-new-cancel').onclick = () => { $('nhc-new').hidden = true; S.nhcPending = null; nhcMsg(''); };
  $('nhc-new').onsubmit = async e => {
    e.preventDefault(); const params = {}; document.querySelectorAll('[data-newparam]').forEach(i => params[i.dataset.newparam] = i.value.trim());
    const name = $('nhc-new-name').value.trim();
    if (await nhcCall('POST', { op: 'add', template: S.nhcPending.tpl, name, params, position: S.nhcPending.position })) { $('nhc-new').hidden = true; S.nhcPending = null; nhcMsg(`✓ added ${name}`); }
  };
  $('nhcbtn').onclick = openNhc; $('nhc-close').onclick = $('nhc-done').onclick = () => $('nhcdlg').close();
  $('nhcdlg').addEventListener('click', e => { if (e.target === $('nhcdlg')) $('nhcdlg').close(); });

}
