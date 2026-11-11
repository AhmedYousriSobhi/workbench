// The Test run workspace (redesign screen 2, issue 509): pick a scenario, see before Play which steps only the CLI can do (U7),
// watch the run, its expectations and the client's eye, then export a report. The library and polling live in scenarios.js.
import { S } from './state.js';
import { $, H, esc, announce } from './util.js';
import { ICON } from './governor.js';
import { ENT_NAME } from './focus.js';
import { buildReport, eyeRows, runVerdict, timelineRows } from './testrunmodel.js';

const ON_RUN = () => !$('tab-run').hidden;
const same = s => !!(s && S.trPrev && s.name === S.trPrev.name);

function eyeNow() {  // [snapshot, base]: after a run its end counters, otherwise the live ones, minus the counters at Play
  const r = S.trRun && S.trPrev && S.trRun.name === S.trPrev.name ? S.trRun : null;
  return [r && r.end ? r.end : S.flowSnap, r ? r.base : null];
}
export function paintRunEye() {
  if (!ON_RUN()) return;
  const [snap, base] = eyeNow(), rows = eyeRows(snap, base, ENT_NAME);
  $('run-eye').innerHTML = !snap ? '<p class="dim small">Waiting for the first reading from the mocks.</p>' : !rows.length ? '<p class="dim small">The simulation stack is not running, so there are no calls to show: <code>./scripts/benchctl start</code></p>' :
    `<div class="tablewrap"><table class="focustab"><thead><tr><th>Service</th><th>${base ? 'Calls during the run' : 'Calls since the page opened'}</th><th>Errors</th><th>Verdict</th></tr></thead><tbody>${rows.map(r =>
      `<tr><td><b>${esc(r.name)}</b></td><td>${(base ? r.during : r.total) == null ? 'n/a' : (base ? r.during : r.total).toLocaleString()}</td><td>${r.errors == null ? 'n/a' : r.errors}</td><td>${esc(r.verdict)}</td></tr>`).join('')}</tbody></table></div>`;
}

export function paintPreview(s) {
  const p = S.trPrev, on = same(s);
  $('run-empty').hidden = !!p || !!s.name; $('runpanel').hidden = !p && !s.name;
  if (!p) return;
  const rows = timelineRows(p, on ? s : null), done = rows.filter(x => x.status !== 'pending').length, verdict = on ? runVerdict(s) : null;
  $('runtitle').textContent = `${p.name}${p.ref ? ` · ${p.ref}` : ''}${on && s.running ? ` (${s.speed}×)` : ''}`;
  $('rundesc').textContent = `${p.description.replace(/[.\s]+$/, '')}. Takes about ${Math.round(p.length)} s at 1×.`;
  const word = { running: `Running · step ${done}/${rows.length}`, stopped: 'Stopped', passed: 'Passed', failed: 'Failed', 'passed with skips': 'Passed, with skipped steps' };
  $('runresult').innerHTML = verdict ? `<span class="result ${verdict === 'passed with skips' ? 'passed' : verdict}">${word[verdict]}</span>` : '<span class="result">Not run yet</span>';
  $('runctl').innerHTML = on && s.running ? '<button class="btn stop" id="stopbtn">■ Stop</button>' : '';
  $('bar').style.width = (rows.length && on ? (done / rows.length) * 100 : 0) + '%';
  const cli = p.cli_only, failed = rows.filter(x => x.status === 'failed').length;
  $('runcli').hidden = !cli || (on && s.running);
  $('runcli').innerHTML = cli ? `<span aria-hidden="true">↷</span> <b>${cli} of ${rows.length + p.expects.length} items need the host CLI.</b> The page marks them “CLI only” and skips them; a skip is not a pass. To run everything: <code id="runcmd">${esc(p.cli)}</code> <button class="btn small" id="runcopy" type="button">Copy</button>` : '';
  $('runnote').innerHTML = on && !s.running && failed ? `<div class="notice">${failed} of ${rows.length} steps failed. If the messages say a service is unreachable, the stack is probably not running: <code>./scripts/benchctl up --profile min</code></div>` : '';
  const cur = on && s.running ? rows.findIndex(x => x.status === 'pending') : -1;
  $('timeline').innerHTML = rows.map((x, i) => {
    const flag = !x.page ? '<span class="clitag"><span aria-hidden="true">↷</span> CLI only</span>' : '';
    return `<li${i === cur ? ' class="cur"' : ''}><span class="stt st-${x.status}"><span aria-hidden="true">${ICON[x.status]}</span> ${x.status}</span>
     <div>${x.at.toFixed(0)} s · ${esc(x.text)} ${flag}${x.note || (!x.page && x.status === 'pending') ? `<div class="small dim">${esc(x.note || x.why)}</div>` : ''}</div></li>`;
  }).join('');
  const got = new Map((on ? s.expects || [] : []).map(x => [x.label, x]));
  $('expects').innerHTML = p.expects.length ? '<h3 class="rh">Expectations</h3>' + p.expects.map(x => {
    const r = got.get(x.label), st = r ? r.status : !x.page ? 'skipped' : 'pending';
    return `<p class="st st-${st}"><span aria-hidden="true">${ICON[st]}</span> ${st}${!x.page && !r ? ' (CLI only)' : ''}: ${esc(x.label)} <span class="dim">${esc(r ? r.detail : '')}</span></p>`;
  }).join('') : '';
  $('trace-out').innerHTML = on && s.trace ? '<h3 class="rh">Slurm trace</h3>' + (s.trace.map(e =>
    `<details class="small"><summary>${esc(e.label)}</summary><pre class="wrap">${esc([e.before && 'before: ' + e.before, ...e.logs.map(r => `log ${r.source} ${r.where}: ${r.line}`),
      ...Object.entries(e.more).map(([k, n]) => `... ${n} more lines in docker logs ${k}`), 'after:  ' + e.after].filter(Boolean).join('\n'))}</pre></details>`).join('')
    || `<p class="dim small">${s.running ? 'The first entry appears when the next step starts.' : 'Nothing to show.'}</p>`) : '';
  const finished = on && !s.running && s.steps && s.steps.length;
  $('run-export').hidden = !finished;
  if ($('stopbtn')) $('stopbtn').onclick = () => fetch('/api/scenarios/stop', { method: 'POST', headers: H });
  if ($('runcopy')) $('runcopy').onclick = async () => { try { await navigator.clipboard.writeText(p.cli); announce('Command copied'); } catch (e) { announce('Select the command and copy it by hand'); } };
}

function download(name, text, type) {  // built here and handed to the browser: nothing is stored on the server
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
export function exportRun(kind) {
  const s = S.trLast, r = S.trRun && s && S.trRun.name === s.name ? S.trRun : null, [snap, base] = eyeNow();
  const rep = buildReport({ preview: S.trPrev, run: s, startedAt: r && r.startedAt, finishedAt: r && r.finishedAt, speed: r && r.speed, eye: eyeRows(snap, base, k => k) });
  const stem = `test-run-${String(rep.data.scenario).replace(/[^\w.-]/g, '_')}`;
  if (kind === 'json') download(stem + '.json', JSON.stringify(rep.data, null, 2), 'application/json'); else download(stem + '.md', rep.markdown, 'text/markdown');
  announce('Report downloaded');
}

export function init() {
  $('run-md').onclick = () => exportRun('md'); $('run-json').onclick = () => exportRun('json');
}
