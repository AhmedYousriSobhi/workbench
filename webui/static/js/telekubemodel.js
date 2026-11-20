// What the Teleport details show for Kubernetes (issue 755): pure functions over /api/teleport-kube, so node --test can run them.
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const COUNTERS = [['requests', 'Kubernetes requests'], ['exec', 'Exec sessions'], ['portforward', 'Port-forward sessions']];
const OUTCOME = code => { const c = Number(code); return c >= 200 && c < 300 ? 'ok' : c >= 400 && c < 500 ? 'refused' : c >= 500 ? 'error' : ''; };  // words, so status is never colour alone

/** Rows for the `kv` list of the Teleport pop-up: [] when the part is hidden, one notice row when Teleport does not answer. */
export function kubeRows(d) {
  if (!d || !d.shown) return [];
  if (d.notice) return [['Kubernetes', esc(d.notice)]];
  const rows = COUNTERS.map(([k, label]) => [label, d.counters?.[k] == null ? 'not measured' : Number(d.counters[k]).toLocaleString()]);
  const ev = d.events || [];
  rows.push(['Recent kube calls', ev.length ? `<ul class="kube-ev">${ev.map(e =>
    `<li><b>${esc(e.user)}</b> ${esc(e.verb)} ${esc(e.resource)} <span class="kube-code" data-outcome="${OUTCOME(e.code)}">${esc(e.code)} ${OUTCOME(e.code)}</span></li>`).join('')}</ul>` : 'none yet']);
  return rows;
}
