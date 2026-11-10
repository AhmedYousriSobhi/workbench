import { S } from './state.js';
import { $, H, esc } from './util.js';
import { setMenu } from './shell.js';
import { ICON, setChip } from './governor.js';

export function scnOpen(on) { const d = $('scndlg'); if (on && !d.open) d.show(); if (!on && d.open) d.close(); $('scn-toggle').setAttribute('aria-expanded', d.open ? 'true' : 'false'); $('scn-toggle').classList.toggle('on', d.open); try { sessionStorage.setItem('workbench-scn', d.open ? '1' : '0'); } catch (e) { /* not remembered */ } }
export async function runChip() {
  clearTimeout(S.chipTimer);
  try {
    const s = await (await fetch('/api/scenarios/run')).json(), c = $('c-run');
    c.hidden = !s.running;
    if (s.running) { const done = s.steps.filter(x => x.status !== 'pending').length; setChip('c-run', 'warn', '▶', `${s.name} running ${done}/${s.steps.length}`); c.onclick = () => scnOpen(true); }
  } catch (e) { /* offline: the connection chip already says so */ }
  S.chipTimer = setTimeout(runChip, document.hidden ? 15000 : 3000);
}


export function describe(s) {
  const d = s.do, n = (d.nodes || []).join(', ');
  if (d.drain) return `Drain ${d.drain.join(', ')} in Slurm (${d.reason || 'scenario'})`;
  if (d.resume) return `Resume ${d.resume.join(', ')} in Slurm`;
  if (d.submit) return `Submit a ${d.submit.duration || 600} s job on ${d.submit.node}${d.submit.gpus ? ` holding ${d.submit.gpus} GPUs` : ''}`;
  if (d.cancel) return 'Cancel the simulated users\' jobs';
  for (const k of ['slurmd_restart', 'slurmd_stop', 'slurmd_start']) if (d[k]) return `${{ slurmd_restart: 'Restart', slurmd_stop: 'Stop', slurmd_start: 'Start' }[k]} slurmd on ${d[k].join(', ')}`;
  if (d.slurmctld_restart) return 'Restart slurmctld';
  if (d.failure) return d.failure.mode ? `Make Jira answer ${d.failure.mode} for the next ${d.failure.count || 1} calls` : 'Jira back to normal';
  if (d.clear) return `Clear injected faults on ${n}`;
  if (d.power) return `Power ${d.power} ${n}`;
  if (d.state) return `Set ${n} to ${d.state}`;
  if (d.xid) return `GPU error XID ${d.xid} on ${n}`;
  if (d.temp) return `Temperature of ${n} to ${d.temp} °C`;
  if (d.fan_fail) return `${d.fan_fail} fan(s) fail on ${n}`;
  return JSON.stringify(d);
}
export function plain(x) {
  if (!x.detail) return '';
  if (/unreachable/.test(x.detail)) return `can't reach ${x.target}: is the stack running?`;
  if (/Slurm steps need/.test(x.detail)) return 'needs the host CLI';
  return x.detail;
}
export async function loadScenarios() {
  const list = await (await fetch('/api/scenarios')).json();
  $('list').innerHTML = list.map(s =>
    `<li class="scn" data-name="${esc(s.name)}"><div class="hd"><b>${esc(s.name)}</b><span class="pbadge">${s.steps} ${s.steps === 1 ? 'step' : 'steps'}</span></div><p>${esc(s.description)}</p>
     <button class="btn" data-name="${esc(s.name)}" aria-label="Play ${esc(s.name)}">▶ Play</button></li>`).join('') || '<li class="dim">No scenarios found.</li>';
  document.querySelectorAll('#list button').forEach(b => b.onclick = () => play(b.dataset.name));
  filterScenarios();
  poll();
}
export function filterScenarios() {
  const q = $('scnq').value.trim().toLowerCase();
  document.querySelectorAll('#list .scn').forEach(li => { li.hidden = !!q && !li.textContent.toLowerCase().includes(q); });
}
export async function play(name) {
  scnOpen(true);
  const r = await fetch(`/api/scenarios/${name}/run?speed=${$('speed').value}${$('trace').checked ? '&trace=1' : ''}`, { method: 'POST', headers: H });
  if (!r.ok) { $('runpanel').hidden = false; $('runnote').innerHTML = `<div class="notice">${esc((await r.json()).detail)}</div>`; return; }
  poll();
}
export async function stopRun() { await fetch('/api/scenarios/stop', { method: 'POST', headers: H }); }
export async function poll() {
  clearTimeout(S.pollTimer);
  const s = await (await fetch('/api/scenarios/run')).json();
  document.querySelectorAll('#list .btn').forEach(b => { b.disabled = !!s.running; });
  document.querySelectorAll('#list .scn').forEach(li => li.classList.toggle('active', !!s.running && li.dataset.name === s.name));
  if (!s.name) return;
  $('runpanel').hidden = false;
  $('runtitle').textContent = 'Run: ' + s.name + (s.running ? ` (${s.speed}×)` : '');
  const done = s.steps.filter(x => x.status !== 'pending').length;
  const verdict = s.running ? 'running' : s.stopped ? 'stopped' : s.ok ? 'passed' : 'failed';
  $('runresult').innerHTML = `<span class="result ${verdict}">${{ running: `Running · step ${done}/${s.steps.length}`, stopped: 'Stopped', passed: 'Passed', failed: 'Failed' }[verdict]}</span>`;
  $('runctl').innerHTML = s.running ? '<button class="btn stop" id="stopbtn">■ Stop</button>' : '';
  if ($('stopbtn')) $('stopbtn').onclick = stopRun;
  $('bar').style.width = (s.steps.length ? (done / s.steps.length) * 100 : 0) + '%';
  const failed = s.steps.filter(x => x.status === 'failed').length;
  $('runnote').innerHTML = !s.running && failed ? `<div class="notice">${failed} of ${s.steps.length} steps failed. If the messages say a service is unreachable, the stack is probably not running: <code>./scripts/benchctl up --profile min</code></div>` : '';
  let cur = s.running ? s.steps.findIndex(x => x.status === 'pending') : -1;
  $('timeline').innerHTML = s.steps.map((x, i) =>
    `<li${i === cur ? ' class="cur"' : ''}><span class="stt st-${x.status}"><span aria-hidden="true">${ICON[x.status]}</span> ${x.status}</span>
     <div>${x.at.toFixed(0)} s · ${esc(describe(x))}${x.detail ? `<div class="small dim">${esc(plain(x))}</div>` : ''}</div></li>`).join('');
  $('expects').innerHTML = s.expects.length ? '<h2 class="rh" style="margin-top:14px">Expectations</h2>' + s.expects.map(x =>
    `<p class="st st-${x.status}" style="font-weight:500"><span aria-hidden="true">${ICON[x.status]}</span> ${x.status}: ${esc(x.label)} <span class="dim">${esc(x.detail)}</span></p>`).join('') : '';
  $('trace-out').innerHTML = s.trace ? '<h2 class="rh" style="margin-top:14px">Slurm trace</h2>' + (s.trace.map(e =>
    `<details class="small"><summary>${esc(e.label)}</summary><pre style="white-space:pre-wrap;overflow-x:auto">${esc([e.before && 'before: ' + e.before, ...e.logs.map(r => `log ${r.source} ${r.where}: ${r.line}`),
      ...Object.entries(e.more).map(([k, n]) => `... ${n} more lines in docker logs ${k}`), 'after:  ' + e.after].filter(Boolean).join('\n'))}</pre></details>`).join('')
    || `<p class="dim small">${s.running ? 'The first entry appears when the next step starts.' : 'Nothing to show.'}</p>`) : '';
  if (s.running) S.pollTimer = setTimeout(poll, 1000);
}

export function init() {
  $('scnbtn').onclick = () => { scnOpen(true); setMenu(false); }; $('scn-close').onclick = () => { scnOpen(false); $('scn-toggle').focus(); }; $('scn-toggle').onclick = () => scnOpen(!$('scndlg').open);
  $('scndlg').addEventListener('keydown', e => { if (e.key === 'Escape') { scnOpen(false); $('scn-toggle').focus(); } });

}

export function init2() {
  $('scnq').oninput = filterScenarios;

}
