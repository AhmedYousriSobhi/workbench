import { $, H, esc, toast } from './util.js';
import { stepRows, verdictView, resultBars, secs, diffTag, validationRows, refusal } from './labmodel.js';

let labs = [], current = null, view = null, report = null;  // the lab list, the one shown, its view and its latest report

async function labGet(url) { const r = await fetch(url); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status); return r; }
export async function loadLabs() {  // the workspace is hidden until a lab exists (W-10)
  try { labs = (await (await labGet('/api/lab')).json()).labs; } catch (e) { labs = []; }
  $('tb-lab').hidden = !labs.length;
  return labs;
}
async function act(action, body) {
  const name = current;
  try {
    const r = await fetch(`/api/lab/${encodeURIComponent(name)}/${action}`, { method: 'POST', headers: { ...H, 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    toast(r.ok ? j.message : '✗ ' + refusal(r.status, j.detail));
  } catch (e) { toast('✗ ' + refusal(503)); }
  setTimeout(paintLab, 1500);
}
async function labDownload(anonymize) {
  const q = `anonymize=${anonymize ? 1 : 0}`;
  try { const t = await (await labGet(`/api/lab/${encodeURIComponent(current)}/report?${q}`)).text(); return t; } catch (e) { toast('✗ ' + e.message); return null; }
}
const bar = (pct, state) => `<meter class="labbar" data-side="${state}" min="0" max="100" value="${pct}" aria-label="${state === 'a' ? 'Baseline' : 'Candidate'} wait, share of the longer one"></meter>`;  // a meter, not a style attribute: the page's CSP forbids inline style
function paintLabView() {
  const v = view, sel = labs.map(l => `<option value="${esc(l.name)}"${l.name === current ? ' selected' : ''}>${esc(l.name)}</option>`).join('');
  const steps = stepRows(v.steps).map((s, i) => `<li data-state="${s.state}"${s.state === 'now' ? ' aria-current="step"' : ''}><b>${i + 1}</b> ${esc(s.label)} <small>${s.state === 'done' ? '✓ done' : s.state === 'now' ? '▶ now' : 'not yet'}</small></li>`).join('');
  const sub = v.plan && v.plan.scale ? v.plan.scale : null;
  const vm = Object.entries(v.version_match || {}).map(([k, s]) => `<li>${esc(k)} ${esc((v.versions || {})[k] || 'not given')}: ${esc(s)}</li>`).join('') || '<li class="dim">Nothing to compare.</li>';
  const diff = v.diff.length ? v.diff.map(d => `<tr><td data-h="File">${esc(d.file)}</td><td data-h="Change">${esc(d.op)} ${esc(d.key || '')}</td><td data-h="From to"><code>${esc(d.from ?? '')}</code> → <code>${esc(d.to ?? '')}</code></td><td data-h="Effect"><span class="labtag">${esc(diffTag(d))}</span> <span class="small dim">${esc(d.note || '')}</span></td></tr>`).join('')
    : `<tr><td colspan="4" class="dim">${v.candidate ? 'The candidate equals the baseline.' : 'No candidate yet: benchctl add candidate ' + esc(v.name)}</td></tr>`;
  const val = validationRows(v.validation).map(r => `<tr><td data-h="Check">${esc(r.tool)}</td><td data-h="Result"><span class="labtag" data-status="${esc(r.status)}">${esc(r.status)}</span></td><td data-h="First message" class="small dim">${esc(r.first)}</td></tr>`).join('')
    || `<tr><td colspan="3" class="dim">Not run: benchctl validate lab ${esc(v.name)}</td></tr>`;
  const vd = report ? verdictView(report.verdict) : null;
  const bars = resultBars(report).map(b => `<tr><td data-h="Partition">${esc(b.partition)}</td><td data-h="A (baseline)">${bar(b.aPct, 'a')} ${secs(b.a)}</td><td data-h="B (candidate)">${bar(b.bPct, 'b')} ${secs(b.b)}</td></tr>`).join('');
  const busy = v.governor && v.governor.state && !['idle', 'done', 'aborted'].includes(v.governor.state);
  $('lab-body').innerHTML = `
    <div class="labhead"><label>Lab <select id="lab-pick">${sel}</select></label><span class="small dim">${v.applied ? `applied: ${esc(v.applied)}` : 'the plain cluster'}${busy ? ` · run ${esc(v.governor.state)} ${v.governor.percent ?? 0} %` : ''}</span></div>
    <ol class="labsteps" aria-label="Lab steps">${steps}</ol>
    <section class="panel"><h3>Imported files</h3><ul class="labfiles">${v.files.map(f => `<li><code>${esc(f.file)}</code> ${esc(f.kind)}</li>`).join('')}</ul><h3>Version match</h3><ul>${vm}</ul>
      <h3>Downscale</h3><p class="small">${sub ? `${sub.production_nodes ?? '?'} production nodes → ${sub.lab_nodes} lab nodes${v.plan.time ? `, time scaled ×${v.plan.time.factor}` : ''}.` : esc((v.plan && v.plan.error) || 'No plan.')}</p></section>
    <section class="panel"><h3>Diff: candidate against baseline</h3><div class="tablewrap"><table><thead><tr><th>File</th><th>Change</th><th>Value</th><th>Effect</th></tr></thead><tbody>${diff}</tbody></table></div></section>
    <section class="panel"><h3>Validation</h3><div class="tablewrap"><table><thead><tr><th>Check</th><th>Result</th><th>First message</th></tr></thead><tbody>${val}</tbody></table></div></section>
    <section class="panel"><h3>Run</h3><p class="small dim">Only at memory level L0 or L1; the governor refuses otherwise and says why.</p>
      <div class="labbtns"><button class="btn small" data-act="apply-baseline">Apply baseline</button><button class="btn small" data-act="apply-candidate"${v.candidate ? '' : ' disabled'}>Apply candidate</button><button class="btn small" data-act="restore"${v.applied ? '' : ' disabled'}>Back to the plain cluster</button>
      <button class="btn small" data-act="run-ab">Run A/B replay</button><button class="btn small" data-act="run-b">Run candidate only</button><button class="btn small" data-act="stop"${busy ? '' : ' disabled'}>Stop run</button></div></section>
    <section class="panel"><h3>Report</h3>${report ? `<p><span class="labverdict" data-state="${vd.state}"><b>${esc(vd.text)}</b></span> <span class="small dim">run ${esc(report.run)}${report.single_run ? ', single run: repeat before relying on it' : ''}</span></p>
      ${(report.suggestions || []).map(s => `<p class="small">• ${esc(s.text)}</p>`).join('')}
      <div class="tablewrap"><table><thead><tr><th>Partition</th><th>Median wait, baseline</th><th>Median wait, candidate</th></tr></thead><tbody>${bars}</tbody></table></div>
      <div class="labbtns"><button class="btn small" data-act="export">Export (anonymized)</button><button class="btn small" data-act="export-raw">Export (full names)</button><button class="btn small" data-act="copy">Copy as PR comment</button></div>` : '<p class="dim">No finished run yet.</p>'}</section>
    <section class="panel"><h3>What this does not test</h3><p class="small">${esc((v.not_tested || []).join(', '))}. Not measured in this version: ${esc((report && report.not_measured || []).join(', ') || 'see the report')}.</p></section>`;
  $('lab-pick').onchange = e => { current = e.target.value; paintLab(); };
  $('lab-body').querySelectorAll('[data-act]').forEach(b => b.onclick = async () => {
    const a = b.dataset.act;
    if (a === 'apply-baseline') act('apply', { side: 'baseline' }); else if (a === 'apply-candidate') act('apply', { side: 'candidate' });
    else if (a === 'restore') act('restore'); else if (a === 'stop') act('stop');
    else if (a === 'run-ab') act('run', { ab: true }); else if (a === 'run-b') act('run', { ab: false });
    else if (a === 'copy') { const t = await labDownload(true); if (t) { try { await navigator.clipboard.writeText(t); toast('Copied the anonymized report'); } catch (e) { toast('✗ Could not copy: use Export'); } } }
    else { const t = await labDownload(a === 'export'); if (t) { const u = URL.createObjectURL(new Blob([t], { type: 'text/markdown' })), l = document.createElement('a'); l.href = u; l.download = 'lab-report.md'; l.click(); URL.revokeObjectURL(u); } }
  });
}
export async function paintLab() {
  await loadLabs();
  const none = !labs.length; $('lab-none').hidden = !none; $('lab-body').hidden = none;
  if (none) return;
  if (!labs.some(l => l.name === current)) current = labs[0].name;
  try {
    view = await (await labGet(`/api/lab/${encodeURIComponent(current)}`)).json();
    report = null;
    const last = view.runs.find(r => r.report);
    if (last) report = await (await labGet(`/api/lab/${encodeURIComponent(current)}/report?format=json&run=${encodeURIComponent(last.run)}`)).json();
    paintLabView();
  } catch (e) { $('lab-body').innerHTML = `<p role="status">✗ ${esc(e.message)}</p>`; }
}
export function init() { loadLabs(); }
