import { S } from './state.js';
import { $, esc } from './util.js';

export const LEVELS = ['Normal', 'Watching', 'Throttling', 'Freezing', 'Stopping', 'Emergency'];
export const MEANING = ['Plenty of free memory. Nothing is being limited.',
  'Free memory is getting low. The governor is watching closely.',
  'Low-priority containers are being throttled.',
  'Memory is tight. Low-priority containers are frozen and reclaimed; no new work is admitted.',
  'Very low. Containers are being stopped to protect the host.', 'Critical. Only the essential services are kept alive.'];
export const ICON = { ok: '✓', failed: '✗', skipped: '↷', pending: '○' };
export const mem = [],
  psi = [];

export function setChip(id, kind, icon, text) {
  const c = $(id); c.className = 'pillchip ' + kind + (c.tagName === 'BUTTON' ? '' : ''); c.innerHTML = `<b aria-hidden="true">${icon}</b> ${esc(text)}`;
}
export function paintAttention() {
  const items = [];
  if (!S.lastGov) items.push(['bad', '✗', 'The governor is not reachable, so memory is not being protected.']);
  else {
    if (S.lastGov.level >= 2) items.push(['bad', '✗', `Memory pressure is at level L${Math.min(S.lastGov.level, 5)}: ${LEVELS[Math.min(S.lastGov.level, 5)].toLowerCase()}.`]);
    (S.lastGov.violations || []).slice(0, 5).forEach(v => items.push(['warn', '▲', 'Security finding: ' + (typeof v === 'string' ? v : JSON.stringify(v))]));
  }
  if (S.lastCl) {
    const down = Object.entries(S.lastCl.services).filter(([, up]) => !up).map(([k]) => k);
    if (down.length === Object.keys(S.lastCl.services).length) items.push(['warn', '▲', 'The simulation stack is not running. Start it with ./scripts/benchctl start']);
    else down.forEach(k => items.push(['bad', '✗', `Service ${k} is not answering.`]));
    S.lastCl.nodes.filter(n => n.state !== 'ok' && !(down.length === Object.keys(S.lastCl.services).length)).forEach(n =>
      items.push([n.state === 'fault' ? 'bad' : 'warn', n.state === 'fault' ? '✗' : '▲', `${n.name}: ${n.label}`]));
  }
  $('attn').innerHTML = items.length ? items.map(([k, ic, t]) => `<li><span class="ic ${k}" aria-hidden="true">${ic}</span><span>${esc(t)}</span></li>`).join('')
    : '<li><span class="ic good" aria-hidden="true">✓</span><span>All clear. Nothing needs you right now.</span></li>';
}
export function paintHealth(c) {
  S.lastCl = c;
  const svc = Object.values(c.services), up = svc.filter(Boolean).length, all = svc.length;
  const bad = c.nodes.filter(n => n.state !== 'ok').length, nn = c.nodes.length, stackDown = up === 0;
  setChip('c-svc', stackDown ? 'bad' : up < all ? 'warn' : 'good', stackDown ? '✗' : up < all ? '▲' : '✓', stackDown ? 'Stack not running' : `Services ${up}/${all}`);
  setChip('c-nodes', stackDown ? 'warn' : bad ? 'warn' : 'good', stackDown ? '–' : bad ? '▲' : '✓', stackDown ? 'Nodes unknown' : `Nodes ${nn - bad}/${nn} healthy`);
  paintAttention();
}
export function spark(id, arr, lo, hi) {
  const span = Math.max(hi - lo, 1e-9);
  const pts = arr.map((v, i) => `${(i / Math.max(arr.length - 1, 1)) * 120},${38 - ((v - lo) / span) * 36}`).join(' ');
  $(id).querySelector('polyline').setAttribute('points', pts);
  $(id).querySelector('polygon').setAttribute('points', arr.length ? `0,40 ${pts} 120,40` : '');
}
export function renderLog() {
  const lines = S.lastLog.filter(l => !$('onlyactions').checked || !l.startsWith('inventory:')).reverse();
  $('logcount').textContent = `(${lines.length})`;
  $('audit').textContent = lines.length ? lines.join('\n') : 'Nothing to show yet: the governor has taken no actions.';
}
export function render(st) {
  const live = $('conn'); live.className = st.ok ? 'live' : ''; live.lastElementChild.textContent = st.ok ? 'live' : 'governor offline';
  const banner = $('banner');
  if (!st.ok) {
    banner.style.setProperty('--lv', 'var(--bad)'); banner.className = 'banner';
    banner.innerHTML = '<i class="orb" aria-hidden="true"></i><div><b>Governor not reachable</b><span class="m">Start it with <code>./scripts/benchctl start</code> (no sudo). Optional boot service: <code>sudo ./scripts/install-governor.sh</code>.</span></div>';
    S.lastGov = null; setChip('c-level', 'bad', '✗', 'Governor offline'); paintAttention();
    return;
  }
  const l = Math.min(st.level, 5);
  banner.style.setProperty('--lv', `var(--L${l})`); banner.className = 'banner' + (l === 0 ? ' calm' : '');
  banner.innerHTML = `<i class="orb" aria-hidden="true"></i><div><b>L${l} · ${LEVELS[l]}${st.dry_run ? ' (dry-run: no actions taken)' : ''}</b><span class="m">${MEANING[l]}${st.violations.length ? ` ${st.violations.length} security finding(s).` : ''}</span></div>`;
  const gb = (st.mem_available || 0) / 2 ** 30;
  S.lastGov = st; setChip('c-level', l <= 1 ? 'good' : l <= 2 ? 'warn' : 'bad', l === 0 ? '✓' : l <= 2 ? '▲' : '✗', `L${l} ${LEVELS[l]}`);
  $('c-mem').textContent = gb.toFixed(1) + ' GB free'; paintAttention();
  $('mem').textContent = gb.toFixed(2) + ' GB'; $('psi').textContent = (st.psi_some ?? 0).toFixed(1) + ' %';
  mem.push(gb); psi.push(st.psi_some ?? 0); if (mem.length > 120) { mem.shift(); psi.shift(); }
  const lo = Math.min(...mem), hi = Math.max(...mem), pad = Math.max(0.25, (1 - (hi - lo)) / 2);
  spark('sp-mem', mem, lo - pad, hi + pad);
  $('mem-lo').textContent = (lo - pad).toFixed(1) + ' GB'; $('mem-hi').textContent = (hi + pad).toFixed(1) + ' GB';
  const phi = Math.max(10, ...psi); spark('sp-psi', psi, 0, phi); $('psi-hi').textContent = phi.toFixed(0) + ' %';
  S.lastLog = st.audit; renderLog();
}

export function init() {
  $('onlyactions').onchange = renderLog;
  const es = new EventSource('/api/events');
  es.onmessage = e => render(JSON.parse(e.data));
  es.onerror = () => { $('conn').className = ''; $('conn').lastElementChild.textContent = 'reconnecting…'; };

}

export function init2() {
  $('c-level').onclick = $('c-mem').onclick = () => $('govdlg').showModal(); $('gov-close').onclick = () => $('govdlg').close();
  $('govdlg').addEventListener('click', e => { if (e.target === $('govdlg')) $('govdlg').close(); });

}
