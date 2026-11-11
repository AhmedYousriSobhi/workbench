import { S } from './state.js';
import { $, H, esc } from './util.js';
import { KIND_NAME, KIND_TEXT } from './geometry.js';
import { kindOf } from './svg.js';
import { nodeList } from './inspector.js';
import { loadCluster } from './cluster.js';
import { jobsWarning, planDiff, reviewLines } from './planmodel.js';

export async function loadPlan() {
  const parts = [...document.querySelectorAll('#pl-parts input:checked')].map(i => i.value).join(',');
  const q = new URLSearchParams({ cpu_nodes: $('pl-cpu').value, gpu_nodes: $('pl-gpu').value, gpus_per_node: $('pl-gpus').value, parts });
  if (S.loginModes.length) q.set('logins', S.loginModes.join(','));
  if (k8sOn()) { q.set('k8s_cpu_nodes', $('pl-kcpu').value); q.set('k8s_nodes', $('pl-kgpu').value); }
  paintPlanSummary();
  q.set('controller', $('pl-ctl').checked ? '1' : '0');
  if (!$('pl-bcmk8sbox').hidden) q.set('bcm_k8s', $('pl-bcmk8s').checked ? '1' : '0');
  if (!$('pl-bmcbox').hidden) { q.set('bmc_groups', [...S.bmcOn].join(',')); const inc = bmcNames().filter(n => !S.bmcOff.has(n)); if (S.bmcOff.size) q.set('bmc_nodes', inc.join(',')); }
  const r = await fetch('/api/plan?' + q);
  if (!r.ok) { $('pl-msg').textContent = '✗ ' + (await r.json()).detail; $('pl-cmd').textContent = ''; $('pl-bar').value = 0; $('pl-need').textContent = '–'; $('pl-free').textContent = ''; $('pl-apply').hidden = true; return; }
  const p = await r.json();
  $('pl-bar').value = Math.min(100, Math.round(100 * p.need_mb / Math.max(p.available_mb, 1)));  // the number admission compares with free memory, not the caps
  $('pl-msg').textContent = (p.fits ? '✓ ' : '✗ ') + p.message;
  $('pl-need').textContent = (p.need_mb / 1024).toFixed(1) + ' GB needed'; $('pl-need').title = `expected use ${p.expected_mb} MB` + (p.running_mb ? `, minus ${p.running_mb} MB already running` : '') + `; the containers may reach ${p.total_mb} MB in total (their caps). Your app is counted as already running (0 MB); benchctl reserves 4096 MB for it unless you pass --app-mb 0.`; $('pl-free').textContent = ` of ${(p.available_mb / 1024).toFixed(1)} GB free`;
  paintReview();
  $('pl-cmd').textContent = p.fits ? p.command : 'Does not fit. Change the selection, or free memory.';
  $('pl-copy').hidden = !p.fits; $('pl-apply').hidden = !p.fits; S.planQuery = q.toString(); syncApply();
  $('pl-copy').onclick = async () => { try { await navigator.clipboard.writeText(p.command); $('pl-copy').textContent = 'Copied'; } catch (e) { $('pl-copy').textContent = 'Select and copy'; } setTimeout(() => { $('pl-copy').textContent = 'Copy command'; }, 1500); };
}
// Review changes (U6): what Apply will add, remove and restart, and whether removing a node stops jobs. Computed in the browser from the
// cluster the page already holds and the Slurm node states read when the sheet opened (planmodel.js).
export function planWanted() {
  const n = id => +$(id).value || 0, sh = S.shapes || {};
  return { nodes: nodeList(), want: { cpu: n('pl-cpu'), gpu: n('pl-gpu'), kcpu: n('pl-kcpu'), kgpu: n('pl-kgpu'), k8s: k8sOn() },
    prefixes: { cpu: (sh.slurm_cpu || {}).prefix || 'cn', gpu: (sh.slurm_gpu || {}).prefix || 'gpu', kcpu: (sh.k8s_cpu || {}).prefix || 'kcn', kgpu: (sh.k8s_gpu || {}).prefix || 'kgpu' },
    states: S.planStates || null, partsNow: S.partsNow || new Set(), partsWant: new Set([...document.querySelectorAll('#pl-parts input:checked')].map(i => i.value)),
    labels: S.partLabels || {}, loginsNow: S.loginsNow || [], loginsWant: S.loginModes.map((m, i) => loginName(i)) };
}
export function paintReview() {
  if (!S.partsDrawn) return;
  const d = planDiff(planWanted()), j = jobsWarning(d);
  $('pl-review').innerHTML = reviewLines(d).map(l => `<li class="rev-${l.kind}"><b class="revk"><span aria-hidden="true">${l.icon}</span> ${esc(l.label)}</b><span>${esc(l.text)}</span></li>`).join('');
  const box = $('pl-jobs'); box.hidden = !j;
  if (j) { box.className = 'revjobs ' + j.kind; box.innerHTML = `<b><span aria-hidden="true">${j.icon}</span> ${esc(j.title)}</b><span class="small">${esc(j.text)}</span>`; }
  $('pl-ackrow').hidden = !(j && j.needsAck); if (!(j && j.needsAck)) $('pl-ack').checked = false;
  if (j && j.needsAck) $('pl-ack-t').textContent = `I understand jobs will stop on ${planDiff(planWanted()).jobs.nodes.join(', ')}`;
  syncApply();
}
export function syncApply() {  // Apply waits for the tick when a removal stops running jobs
  const j = jobsWarning(planDiff(planWanted())), need = !!(j && j.needsAck && !$('pl-ack').checked);
  $('pl-apply').disabled = need || !!S.applying; $('pl-apply').title = need ? 'Tick "I understand" first: running jobs will stop' : '';
}
export async function readStates() {  // one sinfo through the governor (cached 4 s on the server); null when it gives no answer
  try { const s = (await (await fetch('/api/slurm-stats')).json()).stats; S.planStates = s && s.node_states ? s.node_states : null; } catch (e) { S.planStates = null; }
  paintReview();
}
export const k8sInput = () => document.querySelector('#pl-parts input[value="k8s"]'),
  k8sOn = () => !!(k8sInput() && k8sInput().checked);
// BMC rules: a switch per node group, every group on as in real hardware; single nodes are an exception list (CLI: --bmc-groups, --bmc-nodes)
export const BMC_GROUPS = [['slurm-cpu', 'Slurm CPU nodes', 'pl-cpu', 'slurm_cpu'], ['slurm-gpu', 'Slurm GPU nodes', 'pl-gpu', 'slurm_gpu'], ['k8s-cpu', 'Kubernetes CPU nodes', 'pl-kcpu', 'k8s_cpu'], ['k8s-gpu', 'Kubernetes GPU nodes', 'pl-kgpu', 'k8s_gpu']];
S.bmcOn = new Set(BMC_GROUPS.map(g => g[0]));
export const bmcActive = g => !g[0].startsWith('k8s') || k8sOn();
export const bmcCount = g => bmcActive(g) ? +$(g[2]).value || 0 : 0;
export const bmcGroupNames = g => Array.from({ length: bmcCount(g) }, (_, i) => `${(S.shapes[g[3]] || {}).prefix || g[0]}${String(i + 1).padStart(3, '0')}`);
export const bmcNames = () => BMC_GROUPS.filter(g => S.bmcOn.has(g[0])).flatMap(bmcGroupNames);
export function drawBmc() {
  $('pl-bmcgroups').innerHTML = BMC_GROUPS.map(g => `<label class="tog${bmcActive(g) ? '' : ' dim'}"><input type="checkbox" data-g="${g[0]}"${S.bmcOn.has(g[0]) && bmcActive(g) ? ' checked' : ''}${bmcActive(g) ? '' : ' disabled'}><span>${esc(g[1])} <small class="dim">${bmcCount(g)} × ${S.bmcMb} MB</small></span></label>`).join('');
  $('pl-bmcgroups').querySelectorAll('input').forEach(i => i.onchange = () => { i.checked ? S.bmcOn.add(i.dataset.g) : S.bmcOn.delete(i.dataset.g); planInputs(); });
  const names = BMC_GROUPS.filter(g => S.bmcOn.has(g[0])).flatMap(bmcGroupNames), n = names.filter(x => !S.bmcOff.has(x)).length;
  $('pl-bmctotal').textContent = `${n} BMCs × ${S.bmcMb} MB = ${n * S.bmcMb} MB` + (S.bmcOff.size ? ` (${[...S.bmcOff].filter(x => names.includes(x)).length} left out)` : '');
  $('pl-bmcnodes').innerHTML = names.map(x => `<label class="tog"><input type="checkbox" value="${esc(x)}"${S.bmcOff.has(x) ? '' : ' checked'}><span>${esc(x)}</span></label>`).join('');
  $('pl-bmcnodes').querySelectorAll('input').forEach(i => i.onchange = () => { i.checked ? S.bmcOff.delete(i.value) : S.bmcOff.add(i.value); planInputs(); });
}
export function paintPlanSummary() {
  showBmcNodes();  // the BCM-Kubernetes switch shows only with the BCM and Kubernetes parts on
  if (!$('pl-bmcbox').hidden) drawBmc();  // the same numbers the cards show, as one list: how many nodes of each kind, which logins, how many parts
  const n = id => +$(id).value || 0, gpus = n('pl-gpu') * n('pl-gpus'), on = k8sOn(), kg = (S.shapes.k8s_gpu || {}).gpus || 8, kgpus = n('pl-kgpu') * kg;
  $('pc-slurm-sum').textContent = `${n('pl-cpu') + n('pl-gpu')} nodes`;
  $('pc-k8s-sum').textContent = `${n('pl-kcpu') + n('pl-kgpu')} nodes`; $('pc-k8s').classList.toggle('off', !on); $('pl-kon').checked = on;
  document.querySelectorAll('#pc-k8s-body input, #pc-k8s-body button').forEach(el => { el.disabled = !on; });
  const parts = [...document.querySelectorAll('#pl-parts input:checked')].map(i => i.value).filter(v => v !== 'k8s').length;
  const rows = [['Slurm', `${n('pl-cpu')} CPU + ${n('pl-gpu')} GPU nodes · ${gpus} GPUs`], ['Kubernetes', on ? `${n('pl-kcpu')} CPU + ${n('pl-kgpu')} GPU nodes · ${kgpus} GPUs (KWOK)` : 'off'],
    ['Login nodes', S.loginModes.map((m, i) => `${loginName(i)} (${KIND_NAME[entryKind(m)]}, ${LOGIN_LABEL[entryAuth(m)].replace('Direct ', '')})`).join(', ') || 'none'], ['Optional parts', `${parts} selected`]];
  $('pl-sum').innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
}
export function planInputs() { paintPlanSummary(); clearTimeout(S.planTimer); S.planTimer = setTimeout(loadPlan, 250); S.armed = 0; $('pl-apply').textContent = 'Apply to the running workbench'; }
export function currentSize() {
  const nodes = nodeList(), g = nodes.find(n => kindOf(n) === 'gpu');
  return { cpu: nodes.filter(n => n.kind === 'compute').length, gpu: nodes.filter(n => kindOf(n) === 'gpu').length, gpus: g ? g.gpus : null };
}
export const LOGIN_LABEL = { ssh: 'Direct SSH', teleport: 'Teleport only', both: 'Direct SSH and Teleport' };
export const loginName = i => i === 0 ? 'login' : 'login' + (i + 1);
export const entryAuth = m => m.split(':')[0],
  entryKind = m => m.split(':')[1] || 'both',
  entryOf = (a, k) => k === 'both' ? a : `${a}:${k}`;
export const AUTH_SHORT = { ssh: 'SSH', teleport: 'Teleport', both: 'Both' },
  KIND_SHORT = { slurm: 'Slurm', k8s: 'Kubernetes', both: 'Both' };
export function seg(i, field, map, cur) { return `<div class="seg" role="group" aria-label="${field === 'auth' ? 'Way in' : 'Serves'} of ${loginName(i)}">${Object.entries(map).map(([k, v]) => `<button type="button" data-i="${i}" data-f="${field}" data-v="${k}" aria-pressed="${k === cur}">${v}</button>`).join('')}</div>`; }
export function drawLogins() {
  $('pl-loginrows').innerHTML = S.loginModes.map((m, i) => `<div class="lrow"><b class="nm">${loginName(i)}<small>${esc(KIND_TEXT[entryKind(m)])}</small></b><div class="lopts"><div class="lopt"><span>Way in</span>${seg(i, 'auth', AUTH_SHORT, entryAuth(m))}</div><div class="lopt"><span>Serves</span>${seg(i, 'kind', KIND_SHORT, entryKind(m))}</div></div>${i === S.loginModes.length - 1 && i > 0 ? `<button type="button" class="btn small" data-rm="${i}" aria-label="Remove ${loginName(i)}">Remove</button>` : '<span></span>'}</div>`).join('');
  $('pl-loginrows').querySelectorAll('.seg button').forEach(el => el.onclick = () => { const i = +el.dataset.i, m = S.loginModes[i]; S.loginModes[i] = el.dataset.f === 'auth' ? entryOf(el.dataset.v, entryKind(m)) : entryOf(entryAuth(m), el.dataset.v); drawLogins(); loginsChanged(); });
  $('pl-loginrows').querySelectorAll('[data-rm]').forEach(b => b.onclick = () => { S.loginModes.splice(+b.dataset.rm, 1); drawLogins(); loginsChanged(); });
  $('pl-loginadd').disabled = S.loginModes.length >= S.loginMax;
  $('pl-loginmb').textContent = `${S.loginModes.length} × 40 MB`;
}
export function syncTeleport() {  // a Teleport login needs the Teleport part: tick it, and say so
  const tp = document.querySelector('#pl-parts input[value="teleport"]'), need = S.loginModes.some(m => entryAuth(m) !== 'ssh');
  if (tp && need && !tp.checked) tp.checked = true;
  const k8 = document.querySelector('#pl-parts input[value="k8s"]'), needK = S.loginModes.some(m => entryKind(m) === 'k8s');  // a k-login needs the Kubernetes part
  if (k8 && needK && !k8.checked) k8.checked = true;
  $('pl-loginnote').textContent = [need ? 'A Teleport login turns the Teleport part on. A "Teleport only" node publishes no port and refuses key login.' : '', needK ? 'A k-login turns the Kubernetes part on. It has kubectl and the read-only kubeconfigs, and no Slurm configuration.' : ''].filter(Boolean).join(' ');
}
export function loginsChanged() { syncTeleport(); planInputs(); }
export function showBmcNodes() { $('pl-bmcbox').hidden = !document.querySelector('#pl-parts input[value="bmc"]:checked'); $('pl-bcmk8sbox').hidden = !(document.querySelector('#pl-parts input[value="bcm"]:checked') && k8sOn()); }
export async function drawParts() {  // the options come from the backend registry (/api/settings), so a new one needs no page change
  if (S.partsDrawn) return;
  try {
    const j = await (await fetch('/api/settings')).json();
    $('pl-parts').innerHTML = [...new Set(j.parts.map(p => p.category))].map(c => `<div class="partgroup" role="group" aria-label="${esc(c)}"><b class="small dim">${esc(c)}</b> ` + j.parts.filter(p => p.category === c).map(p => `<label class="tog" title="${esc(p.help)}"><input type="checkbox" value="${esc(p.id)}"${p.on ? ' checked' : ''}><span>${esc(p.label)} <small class="dim">+${p.mb} MB</small></span></label>`).join('') + '</div>').join('');
    $('pl-parts').querySelectorAll('label').forEach(l => { if (l.querySelector('input[value="k8s"]')) l.hidden = true; });  // Kubernetes has its own card
    $('pl-parts').querySelectorAll('.partgroup').forEach(g => { if (![...g.querySelectorAll('label')].some(l => !l.hidden)) g.hidden = true; });
    $('pl-parts').querySelectorAll('input').forEach(i => i.oninput = i.onchange = () => { showBmcNodes(); planInputs(); });
    S.partsNow = new Set(j.parts.filter(x => x.on).map(x => x.id)); S.partLabels = Object.fromEntries(j.parts.map(x => [x.id, x.label]));
    S.shapes = j.shapes || {}; const sb = S.shapes.bmc || {}; S.bmcOn = new Set(sb.groups || BMC_GROUPS.map(g => g[0]));  // what is built now; nothing saved means every group
    S.bmcOff = new Set(); if (sb.nodes) { const keep = new Set(sb.nodes); S.bmcSaved = keep; }
    $('pl-bmcmb').textContent = S.bmcMb = j.bmc_node_mb;
    const lg = j.logins;  // an older page backend does not send it: the parts above still work, the login rows are hidden
    if (lg) { S.loginModes = [...(lg.now || ['ssh'])]; S.loginsNow = S.loginModes.map((m, i) => loginName(i)); S.loginMax = lg.max; drawLogins(); syncTeleport(); }
    else { $('pl-loginadd').disabled = true; $('pl-loginrows').textContent = 'Login nodes need the newer page backend. Run ./scripts/benchctl stop, then ./scripts/benchctl start, and reload.'; }
    $('pl-ctl').checked = !!(S.shapes.controller || {}).enabled;
    $('pl-bcmk8s').checked = !!(S.shapes.bcm || {}).manage_kubernetes || !S.shapes.bcm;
    showBmcNodes(); S.partsDrawn = true;
  } catch (e) { $('pl-parts').textContent = 'Could not load the options. The page and its backend may be different versions: run ./scripts/benchctl stop, then ./scripts/benchctl start, and reload.'; }
}
export async function openPlan() {
  await drawParts();
  const c = currentSize(), sh = S.shapes;
  if (c.cpu + c.gpu) { $('pl-cpu').value = c.cpu; $('pl-gpu').value = c.gpu; if (c.gpus) $('pl-gpus').value = c.gpus; }
  if (sh.k8s_cpu) { $('pl-kcpu').value = sh.k8s_cpu.count; $('pl-kgpu').value = sh.k8s_gpu.count; }
  const node = (o, unit) => o && o.cpus ? `${o.cpus} CPUs · ${o.memory_gb} GB each${unit || ''}` : '';
  $('pl-shape-cpu').textContent = node(sh.slurm_cpu); $('pl-shape-gpu').textContent = sh.slurm_gpu && sh.slurm_gpu.gpu_type ? `${sh.slurm_gpu.gpu_type.toUpperCase()} GPUs` : '';
  $('pl-shape-kcpu').textContent = node(sh.k8s_cpu); $('pl-shape-kgpu').textContent = sh.k8s_gpu && sh.k8s_gpu.gpus ? `${sh.k8s_gpu.gpus} × ${(sh.k8s_gpu.gpu_type || '').toUpperCase()}, ${sh.k8s_gpu.cpus} CPUs each` : '';
  $('pl-now-gpus').textContent = c.gpus ? `now ${c.gpus}` : '';
  if (S.bmcSaved) { S.bmcOff = new Set(bmcNames().filter(n => !S.bmcSaved.has(n))); S.bmcSaved = null; }  // a saved node list shows as exceptions
  $('pl-apply-msg').textContent = ''; S.armed = 0; paintPlanSummary(); $('plandlg').showModal(); loadPlan(); S.planStates = null; readStates();
}

export function init() {
  document.querySelectorAll('#plandlg input[type=number]').forEach(i => i.oninput = i.onchange = () => planInputs());
  document.querySelectorAll('[data-step]').forEach(b => b.onclick = () => { const i = $(b.dataset.step); i.value = Math.min(+i.max, Math.max(+i.min, (+i.value || 0) + +b.dataset.d)); planInputs(); });
  const KIND_LABEL = { both: 'Slurm and Kubernetes (login)', slurm: 'Slurm only (s-login)', k8s: 'Kubernetes only (k-login)' };
  $('pl-lg-one').onclick = () => { S.loginModes = ['ssh']; drawLogins(); loginsChanged(); };
  $('pl-lg-split').onclick = () => { S.loginModes = ['ssh:slurm', 'ssh:k8s']; drawLogins(); loginsChanged(); };
  $('pl-kon').onchange = () => { const k = k8sInput(); if (k) k.checked = $('pl-kon').checked; if (!$('pl-kon').checked) { S.loginModes = S.loginModes.map(m => entryKind(m) === 'k8s' ? entryOf(entryAuth(m), 'slurm') : m); drawLogins(); } syncTeleport(); planInputs(); };
  $('pl-loginadd').onclick = () => { if (S.loginModes.length < S.loginMax) { S.loginModes.push('ssh'); drawLogins(); loginsChanged(); } };
  $('pl-bcmk8s').onchange = $('pl-ctl').onchange = () => planInputs();
  $('pl-ack').onchange = syncApply;

}

export function init2() {
  $('planbtn').onclick = openPlan; $('plan-close').onclick = $('plan-cancel').onclick = () => $('plandlg').close();
  $('plandlg').addEventListener('click', e => { if (e.target === $('plandlg')) $('plandlg').close(); });  // a click on the dim backdrop closes it

}

export function init3() {
  $('pl-apply').onclick = async () => {
    if (!S.armed) {  // first press asks; the second one (within 8 s) applies
      S.armed = Date.now(); $('pl-apply').textContent = 'Confirm: rebuild now';
      $('pl-apply-msg').textContent = $('pl-review').textContent.includes('Removes') ? 'Review the changes above: removed nodes lose their jobs. Press again to apply.' : 'Press again to apply.';
      setTimeout(() => { if (S.armed && Date.now() - S.armed >= 8000) { S.armed = 0; $('pl-apply').textContent = 'Apply to the running workbench'; $('pl-apply-msg').textContent = ''; } }, 8100);
      return;
    }
    S.armed = 0; $('pl-apply').textContent = 'Apply to the running workbench';
    const before = new Set(S.lastLog.filter(l => l.includes('apply-plan')));
    S.applying = true; $('pl-apply').disabled = true; $('pl-apply-msg').textContent = 'Asking the governor…';
    const r = await fetch('/api/plan/apply?' + S.planQuery, { method: 'POST', headers: H });
    const j = await r.json();
    if (!r.ok) { $('pl-apply-msg').textContent = '✗ ' + j.detail; S.applying = false; syncApply(); return; }
    $('pl-apply-msg').textContent = (j.started ? '▶ ' : '▲ ') + j.message;
    if (!j.started) { S.applying = false; syncApply(); return; }
    const t0 = Date.now(), timer = setInterval(() => {
      const done = S.lastLog.find(l => l.includes('apply-plan') && /: (done|FAILED)|^FAILED /.test(l) && !before.has(l));
      if (done || Date.now() - t0 > 600000) {
        clearInterval(timer); S.applying = false; syncApply(); readStates();
        $('pl-apply-msg').textContent = done ? (done.includes('FAILED') ? '✗ ' : '✓ ') + done : '▲ Still running after 10 minutes: check Governor activity.';
        S.connCache = null; S.lastCluster = ''; loadCluster();  // new parts or nodes: Connections and node panels read again (U4)
      } else { S.lastCluster = ''; loadCluster(); }
    }, 4000);
  };

}
