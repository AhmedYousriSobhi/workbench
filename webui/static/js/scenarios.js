import { S } from './state.js';
import { $, H, esc } from './util.js';
import { selectTab } from './shell.js';
import { setChip } from './governor.js';
import { eyeBase } from './testrunmodel.js';
import { paintRunEye, paintPreview } from './testrun.js';

export function scnOpen(on) { if (on) selectTab('run'); }  // the Scenarios drawer became the Test run workspace (issue 509)
export async function runChip() {
  clearTimeout(S.chipTimer);
  try {
    const s = await (await fetch('/api/scenarios/run')).json(), c = $('c-run');
    c.hidden = !s.running;
    if (s.running) { const done = s.steps.filter(x => x.status !== 'pending').length; setChip('c-run', 'warn', '▶', `${s.name} running ${done}/${s.steps.length}`); c.onclick = () => scnOpen(true); }
  } catch (e) { /* offline: the connection chip already says so */ }
  S.chipTimer = setTimeout(runChip, document.hidden ? 15000 : 3000);
}


export async function loadScenarios() {
  const list = await (await fetch('/api/scenarios')).json();
  $('list').innerHTML = list.map(s =>
    `<li class="scn" data-name="${esc(s.name)}"><button type="button" class="scnsel" data-name="${esc(s.name)}" aria-label="Show ${esc(s.name)}"><span class="hd"><b>${esc(s.name)}</b><span class="pbadge">${s.steps} ${s.steps === 1 ? 'step' : 'steps'}</span></span><span class="scnd">${esc(s.description)}</span></button>
     <button class="btn" data-name="${esc(s.name)}" aria-label="Play ${esc(s.name)}">▶ Play</button></li>`).join('') || '<li class="dim">No scenarios found.</li>';
  document.querySelectorAll('#list .btn').forEach(b => b.onclick = () => play(b.dataset.name));
  document.querySelectorAll('#list .scnsel').forEach(b => b.onclick = () => selectScenario(b.dataset.name));
  filterScenarios();
  poll();
}
export function filterScenarios() {
  const q = $('scnq').value.trim().toLowerCase();
  document.querySelectorAll('#list .scn').forEach(li => { li.hidden = !!q && !li.textContent.toLowerCase().includes(q); });
}
export async function selectScenario(name) {  // what the scenario will do, and which steps only the CLI can do, before anything runs (U7)
  const r = await fetch(`/api/scenarios/${encodeURIComponent(name)}`);
  if (!r.ok) { $('runpanel').hidden = false; $('runnote').innerHTML = `<div class="notice">${esc((await r.json()).detail)}</div>`; return; }
  S.trPrev = await r.json();
  poll();
}
export async function play(name) {
  scnOpen(true);
  if (!S.trPrev || S.trPrev.name !== name) await selectScenario(name);
  const r = await fetch(`/api/scenarios/${encodeURIComponent(name)}/run?speed=${$('speed').value}${$('trace').checked ? '&trace=1' : ''}`, { method: 'POST', headers: H });
  if (!r.ok) { $('runpanel').hidden = false; $('runnote').innerHTML = `<div class="notice">${esc((await r.json()).detail)}</div>`; return; }
  S.trRun = { name, startedAt: new Date().toISOString(), finishedAt: null, speed: +$('speed').value, base: eyeBase(S.flowSnap), end: null };  // counters at Play: the report shows the difference
  poll();
}
export async function stopRun() { await fetch('/api/scenarios/stop', { method: 'POST', headers: H }); }
export async function poll() {
  clearTimeout(S.pollTimer);
  const s = await (await fetch('/api/scenarios/run')).json();
  document.querySelectorAll('#list .btn').forEach(b => { b.disabled = !!s.running; });
  const shown = S.trPrev ? S.trPrev.name : s.name;
  document.querySelectorAll('#list .scn').forEach(li => { li.classList.toggle('active', !!s.running && li.dataset.name === s.name); li.classList.toggle('sel', li.dataset.name === shown); });
  if (s.name && (!S.trPrev || (s.running && S.trPrev.name !== s.name))) {  // a run started elsewhere (CLI, another tab): show it
    const r = await fetch(`/api/scenarios/${encodeURIComponent(s.name)}`); if (r.ok) S.trPrev = await r.json();
  }
  if (S.trRun && S.trRun.name === s.name && !s.running && !S.trRun.finishedAt) { S.trRun.finishedAt = new Date().toISOString(); S.trRun.end = eyeBase(S.flowSnap) && { ...eyeBase(S.flowSnap), reachable: { ...S.flowSnap.reachable } }; }
  S.trLast = s;
  paintPreview(s);
  paintRunEye();
  if (s.running) S.pollTimer = setTimeout(poll, 1000);
}

export function init() {
  $('scn-toggle').onclick = () => scnOpen(true);
}

export function init2() {
  $('scnq').oninput = filterScenarios;
}
