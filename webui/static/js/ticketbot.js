// The Ticket bot sheet (issue 728): the fake engineers' pace and shares, changed while the bot runs. Only the jira-mock's alert tickets.
import { $, H } from './util.js';

const FIELDS = [['every', 'tb-every'], ['resolve', 'tb-resolve'], ['ignore', 'tb-ignore'], ['reopen', 'tb-reopen']];
const msg = (t) => { $('tb-msg').textContent = t; };
const KEY = { resolve_after: 'resolve' };  // the bot's own name for the resolve time

export function fromForm() {  // only the boxes that hold a number
  const out = {};
  for (const [k, id] of FIELDS) { const v = $(id).value.trim(); if (v !== '' && Number.isFinite(Number(v))) out[k] = Number(v); }
  return out;
}
export async function openTicketBot() {
  msg(''); for (const [, id] of FIELDS) $(id).value = '';
  try {
    const r = await fetch('/api/ticket-bot'), j = await r.json();
    if (!r.ok) msg('✗ ' + (j.detail || r.status));
    else { for (const [k, v] of Object.entries(j.settings || {})) { const f = KEY[k] || k, el = FIELDS.find(x => x[0] === f); if (el) $(el[1]).value = v; }
      msg(Object.keys(j.settings || {}).length ? '' : 'The bot runs with the values it was started with (empty boxes). Start it with benchctl load on --tickets.'); }
  } catch (e) { msg('✗ Could not reach the page server.'); }
  $('tbdlg').showModal();
}
export async function apply(e) {
  e.preventDefault(); const body = fromForm();
  if (!Object.keys(body).length) return msg('Enter at least one value.');
  try {
    const r = await fetch('/api/ticket-bot', { method: 'POST', headers: { ...H, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }), j = await r.json().catch(() => ({}));
    msg(r.ok ? '✓ ' + j.message : '✗ Refused, nothing changed: ' + (j.detail || r.status));
  } catch (err) { msg('✗ Could not reach the page server.'); }
}
export function init() {
  $('tbbtn').onclick = () => { $('menu').hidden = true; $('menubtn').setAttribute('aria-expanded', 'false'); openTicketBot(); };
  $('tb-form').addEventListener('submit', apply);
  $('tb-close').onclick = $('tb-done').onclick = () => $('tbdlg').close();
  $('tbdlg').addEventListener('click', e => { if (e.target === $('tbdlg')) $('tbdlg').close(); });
}
