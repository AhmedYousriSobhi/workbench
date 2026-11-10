import { S } from './state.js';
import { REAL_SERVICES, accUp, hasTeleport, scoped, svcList } from './flowstate.js';
import { $, esc } from './util.js';
import { bindCopy, connections, paintConnections } from './connections.js';
import { STATE_ICON } from './cluster.js';
import { leaveNode, showNode } from './node.js';
import { kindOf } from './svg.js';
import { KIND_NAME, KIND_TEXT, MODE_TEXT, SVC_METHOD, loginKind, loginList, loginRuns } from './geometry.js';
import { selectTab } from './shell.js';

export function nodeList() { try { return S.lastCluster ? scoped().nodes : []; } catch (e) { return []; } }
export const SVC_CONN = { 'bmc-farm': 'bmc', 'bcm-mock': 'bcm', 'jira-mock': 'jira', 'jira-real': 'jira-real', 'k8s-api': 'k8s', 'gpu-exporter': 'gpu', 'dcgm-exporter': 'dcgm', 'real-gpu': 'real-gpu', 'teleport': 'teleport' };  // diagram box -> Connections row
// hover tip and click pop-up on every part of the diagram (#192). The tip is one line of state; the pop-up has the address, the command and the actions.
export const tipEl = () => $('tip');
export function showTip(el, lines) {
  if (!lines || !$('pop').hidden && $('pop').contains(el)) return;
  const t = tipEl(); t.innerHTML = `<b>${esc(lines[0])}</b><span>${esc(lines[1] || '')}</span>`; t.hidden = false;
  const r = el.getBoundingClientRect(), w = t.offsetWidth, h = t.offsetHeight;
  t.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + 'px';
  t.style.top = (r.top - h - 8 < 8 ? r.bottom + 8 : r.top - h - 8) + 'px';
}
export function hideTip() { tipEl().hidden = true; }
export function asButton(el, name, act, tip) {  // an SVG shape that behaves like a button: click, Enter or Space, announced by name; `tip` gives the hover line
  el.setAttribute('class', 'hit'); el.setAttribute('role', 'button'); el.setAttribute('tabindex', '0'); el.setAttribute('aria-label', name);
  el.addEventListener('click', e => { e.stopPropagation(); act(); }); el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(); } });
  if (tip) { for (const ev of ['pointerenter', 'focus']) el.addEventListener(ev, () => showTip(el, tip())); for (const ev of ['pointerleave', 'blur']) el.addEventListener(ev, hideTip); }
  return el;
}
export const CLIENT_INFO = {
  'Monitoring app': 'A desktop client: SSH, Slurm, Grafana, Jira, BMC and BCM over the profile exported from this page.',
  'Prometheus': 'Scrapes the GPU exporter and the governor; its address is in Connections.',
  'curl · scripts': 'Anything you run by hand against the services; each request shows as a dot.',
  'You: ssh · tsh': 'A terminal: ssh to a login node that takes a key, tsh to one behind Teleport.'
};
export function hidePop(restore) { $('pop').hidden = true; paintInspector(); if (restore && S.popOpener && document.contains(S.popOpener)) S.popOpener.focus(); }
export const dockWide = () => matchMedia('(min-width:1100px)').matches;  // wide: the selected part is shown in the Inspector rail; narrow: a pop-up beside the part (#319)
export async function paintReach() {  // the Connections table in short, for when nothing is selected: part, address, copy the command
  const rows = await connections(); if (!rows.length) return;
  $('reach').innerHTML = rows.map(e => `<li><b>${esc(e.name)}</b><code>${esc(e.address)}</code><button class="btn small copy" data-copy="${esc(e.command)}" aria-label="Copy the command for ${esc(e.name)}">Copy</button></li>`).join(''); bindCopy();
}
export function showPane(which) { for (const [k, id] of [['conn', 'ipane-conn'], ['time', 'dock']]) { $(id).hidden = k !== which; $('it-' + k).setAttribute('aria-pressed', k === which); } }
export const paintInspector = () => {
  const sel = !$('pop').hidden || !$('nodepane').hidden;
  $('inspect-empty').hidden = sel || !!S.focusId || !!S.blastNow || S.lens === 'client'; $('nodeswrap').classList.toggle('detail', sel); if (sel) showPane('conn');
};
export function setKey(open, restore) {
  $('ckey').hidden = !open; $('keybtn').setAttribute('aria-expanded', open); if (!open && restore) $('keybtn').focus();
}
export const clusterNow = () => { try { return scoped(); } catch (e) { return { nodes: [], services: {}, logins: [] }; } };
export async function popInfo(kind, id) {
  const rows = await connections(), row = k => rows.find(r => r.id === k), c = clusterNow(), tp = hasTeleport();
  const conn = (r, extra = []) => r ? [['Address', `<code>${esc(r.address)}</code>`], ...extra, ['Credential', r.credential ? `<code>${esc(r.credential)}</code>` : 'none']] : [['Details', 'Open Connections']];
  if (kind === 'node') {
    const n = c.nodes.find(x => x.name === id) || { name: id, label: '', state: 'unknown', kind: '' }, cx = row('nodes');
    let ip = ''; try { if (n.kind !== 'k8s') { const b = await (await fetch(`/api/nodes/${id}`)).json(); ip = b.ip || ''; } } catch (e) { /* the pop-up still opens */ }
    return { title: id, badge: `${STATE_ICON[n.state] || '?'} ${n.label}`, cls: n.state, cmd: cx ? cx.command.replace('<node>', id) : '', kv: n.kind === 'k8s' ? [['Kind', `Kubernetes node (simulated by KWOK)${n.gpus ? `, ${n.gpus} GPUs` : ''}`], ['Reached by', 'kubectl from a login node that serves Kubernetes (k-login); it has no SSH, BMC or Slurm side']] : [['Kind', kindOf(n) === 'gpu' ? 'GPU node' : 'Compute node'], ['Address', ip ? `<code>${esc(ip)}</code> (private, from a login node)` : 'see Connections'],
      ['Reached by', 'ssh from any s-login (jump host)' + (tp ? ', or through the Teleport proxy' : '')]], actions: n.kind === 'k8s' ? [{ label: 'Connections', fn: () => openConnection('k8s') }] : [{ label: 'Actions and details', fn: () => showNode(id) }, { label: 'Connections', fn: () => openConnection('nodes') }] };
  }
  if (kind === 'login') {
    const l = loginList().find(x => x.name === id) || { mode: 'ssh' }, r = row(id);
    return { title: id, badge: loginRuns(l) ? `${KIND_NAME[loginKind(l)]} · ${MODE_TEXT[l.mode]}` : '✗ not running', cls: loginRuns(l) ? '' : 'fault', cmd: r ? r.command : '', kv: [['Status', loginRuns(l) ? 'running (answers on port 22)' : 'not running: it is in the selection, but nothing answers on its port 22. Build it with ./scripts/benchctl up'], ['Way in', l.mode === 'teleport' ? 'Teleport only: no published port, a key is refused' : l.mode === 'both' ? 'Direct SSH with a key, or Teleport' : 'Direct SSH with a key'], ...conn(r),
      ['Serves', KIND_TEXT[loginKind(l)]], ['To the nodes', loginKind(l) === 'k8s' ? 'kubectl from here (read-only kubeconfig in ~/.kube); no Slurm nodes' : 'ssh from here (jump host)' + (loginKind(l) === 'both' && c.nodes.some(n => n.kind === 'k8s') ? '; kubectl for the Kubernetes nodes' : '')]], actions: [{ label: 'Connections', fn: () => openConnection(id) }] };
  }
  if (kind === 'acc') {  // the jump host and the controller (ADR 0036)
    const r = row(id), up = accUp(id), isJ = id === 'jump';
    return { title: isJ ? 'Jump host' : 'Controller', badge: up ? '✓ up' : '✗ not reachable', cls: up ? 'ok' : 'fault', cmd: r ? r.command : '',
      kv: [['What it is', isJ ? 'A bare ssh bastion, nothing else installed. Reach a key-login node or the controller with ssh -J; Teleport nodes keep Teleport' : 'The slurmctld host and BCM head node. Only whitelisted admins (users or groups in sim.yaml controller.allow) get a shell'],
        ['Status', up ? 'answers on its ssh port' : 'not answering: build it with ./scripts/benchctl up'], ...conn(r)], actions: [{ label: 'Connections', fn: () => openConnection(id) }] };
  }
  if (kind === 'svc') {
    const r = row(SVC_CONN[id]), l = svcList().find(x => x[0] === id) || [id, id], up = S.flowSnap?.reachable[id], t = S.flowSnap?.totals[id] || 0;
    return { title: l[1], badge: up ? '✓ up' : S.flowSnap ? '✗ not reachable' : 'checking', cls: up ? 'ok' : 'fault', cmd: r ? r.command : '', kv: [['Call it with', SVC_METHOD[id] || ''], ['Seen', REAL_SERVICES.has(id) ? 'no request counter (up or down only)' : `${t.toLocaleString()} ${id === 'teleport' ? 'connections' : 'requests'}`], ...conn(r)], actions: [{ label: 'Connections', fn: () => openConnection(SVC_CONN[id]) }] };
  }
  return { title: id, badge: '', cmd: '', kv: [['What it is', esc(CLIENT_INFO[id] || '')]], actions: [{ label: 'Connections', fn: () => openConnection(null) }] };
}
export async function openPop(anchor, kind, id) {
  hideTip(); S.popOpener = anchor;
  const d = await popInfo(kind, id), p = $('pop');
  p.innerHTML = `<div class="pop-h"><b id="pop-t">${esc(d.title)}</b>${d.badge ? `<span class="pill ${d.cls || ''}">${esc(d.badge)}</span>` : ''}<button class="btn small" id="pop-x" aria-label="Close details">✕</button></div>
    <dl class="kv">${d.kv.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>
    ${d.cmd ? `<div class="pop-cmd"><code>${esc(d.cmd)}</code><button class="btn small" id="pop-copy">Copy</button></div>` : ''}
    <div class="pop-act">${d.actions.map((a, i) => `<button class="btn small" data-a="${i}">${esc(a.label)}</button>`).join('')}</div>`;
  p.hidden = false;
  if (dockWide()) { leaveNode(); p.style.left = p.style.top = ''; }  // docked in the Inspector: no positioning, and it replaces a node panel
  else {
    const r = anchor.getBoundingClientRect(), w = p.offsetWidth, h = p.offsetHeight;  // fixed to the window: it always fits on screen, beside the part it describes
    let left = r.right + 10; if (left + w > innerWidth - 8) left = r.left - w - 10; left = Math.max(8, left);
    p.style.left = left + 'px'; p.style.top = Math.max(8, Math.min(r.top - 8, innerHeight - h - 8)) + 'px';
  }
  paintInspector();
  $('pop-x').onclick = () => hidePop(true);
  if ($('pop-copy')) $('pop-copy').onclick = async () => { try { await navigator.clipboard.writeText(d.cmd); $('pop-copy').textContent = 'Copied'; } catch (e) { $('pop-copy').textContent = 'Select and copy'; } };
  p.querySelectorAll('[data-a]').forEach(b => b.onclick = () => { hidePop(false); d.actions[+b.dataset.a].fn(); });
  $('pop-x').focus();
}
export async function openConnection(id) {  // the Connections tab, with the row for this part marked
  selectTab('conn'); await paintConnections();
  const row = id && document.querySelector(`#conn-rows tr[data-id="${id}"]`); if (!row) return;
  document.querySelectorAll('#conn-rows tr.focus').forEach(r => r.classList.remove('focus')); row.classList.add('focus'); row.scrollIntoView({ block: 'center' });
}

export function init() {
  $('it-conn').onclick = () => showPane('conn'); $('it-time').onclick = () => showPane('time');
  $('keybtn').onclick = e => { e.stopPropagation(); setKey($('ckey').hidden); };
  document.addEventListener('click', e => { if (!$('ckey').hidden && !$('ckey').contains(e.target)) setKey(false); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('ckey').hidden) { e.stopPropagation(); setKey(false, true); } }, true);
  const bare = s => String(s || '').replace(/`/g, '');
  document.addEventListener('click', e => { if (!dockWide() && !$('pop').hidden && !$('pop').contains(e.target)) hidePop(false); });  // docked: it stays until you pick something else or press Escape
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('pop').hidden) { e.stopPropagation(); hidePop(true); } }, true);
  addEventListener('scroll', hideTip, { passive: true });

}
