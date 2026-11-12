import { S } from './state.js';
import { $, H, enc, esc, toast } from './util.js';
import { STATE_ICON, loadCluster } from './cluster.js';
import { kindOf } from './svg.js';
import { dockWide, hidePop, paintInspector } from './inspector.js';
import { bindCopy, connections } from './connections.js';
import { setTree } from './shell.js';
import { nodeSummary } from './nodemodel.js';

export function paintNodeActions(name) {
  const c = JSON.parse(S.lastCluster || '{"nodes":[],"actions":[]}'), n = c.nodes.find(x => x.name === name), fix = new Set(['power-on', 'clear']);
  const btns = (rows) => rows.map(a => `<button class="btn" data-node="${esc(name)}" data-action="${a.id}">${esc(a.label)}</button>`).join('');
  const ok = n ? c.actions.filter(a => !a.gpu_only || kindOf(n) === 'gpu') : [];
  $('nodeacts-fault').innerHTML = btns(ok.filter(a => !fix.has(a.id))); $('nodeacts-fix').innerHTML = btns(ok.filter(a => fix.has(a.id)));
  document.querySelectorAll('#nodepane .actions .btn').forEach(b => b.onclick = () => nodeAction(b.dataset.node, b.dataset.action, b.textContent));
  const jobs = n && kindOf(n) === 'gpu' ? [['1 GPU, 5 min', 1, 300], [`All ${n.gpus} GPUs, 5 min`, n.gpus, 300], ['All GPUs, 30 min', n.gpus, 1800]] : [['1 CPU, 5 min', 0, 300], ['1 CPU, 30 min', 0, 1800]];
  $('nodeacts-job').innerHTML = n ? jobs.map(([l, g, d]) => `<button class="btn" data-gpus="${g}" data-dur="${d}">Run ${esc(l)}</button>`).join('') : '';
  document.querySelectorAll('#nodeacts-job .btn').forEach(b => b.onclick = () => nodeJob(name, +b.dataset.gpus, +b.dataset.dur));
  if (n) { $('nodestate').className = 'pill ' + n.state; $('nodestate').textContent = `${STATE_ICON[n.state]} ${n.label}`; }
}
export function leaveNode() {
  S.openNode = null; $('nodepane').hidden = true; $('tree').hidden = false; $('treenote').hidden = false; paintInspector();
  document.querySelectorAll('.node.sel').forEach(b => b.classList.remove('sel'));
}
export function closeNode() {
  leaveNode();
  if (S.openerEl && document.contains(S.openerEl)) S.openerEl.focus(); else { const b = document.querySelector('.node'); if (b) b.focus(); }
}
export async function showNode(name) {
  setTree(true);  // the panel lives in the node column: open it if it was folded away
  S.openNode = name; S.openerEl = document.activeElement; paintNodeActions(name); $('nodeactmsg').textContent = '';
  if (!dockWide()) { $('tree').hidden = true; $('treenote').hidden = true; }  // narrow: the panel takes the list's place; wide: the list stays and the panel is in the Inspector
  hidePop(false); $('nodepane').hidden = false; paintInspector(); $('nodetitle').textContent = name; $('node-back').focus();
  document.querySelectorAll('.node').forEach(b => b.classList.toggle('sel', b.dataset.node === name));
  const r = await fetch(`/api/nodes/${enc(name)}`), body = r.ok ? await r.json() : null;
  $('nodetitle').textContent = name;
  const cx = (await connections()).find(e => e.id === 'nodes'), kv = [];
  if (cx) kv.push(['SSH', `<code>${esc(cx.command.replace('<node>', name))}</code>`], ['User and key', esc(cx.how.split('.')[0]) + ` · key file <code>${esc(cx.credential)}</code>`]);
  const jump = cx && (cx.actions || []).find(a => a.kind === 'copy');
  if (jump) kv.push(['As a user, from a terminal', `<button class="btn small copy" data-copy="${esc(jump.value.replace('<node>', name))}" aria-label="Copy the ssh command for ${esc(name)}">Copy benchctl ssh command</button>`]);
  const bm = (await connections()).find(e => e.id === 'bmc');
  kv.push(['BMC', `<code>${esc(bm ? bm.address : '')}</code> (HTTPS) with Host header <code>bmc-${esc(name)}</code>, user admin, password in <code>compose/generated/secrets/bmc_credentials</code>`],
    ['IP address', body && body.ip ? `<code>${esc(body.ip)}</code> (fixed; reach it from the login node)` : `run <code>./scripts/benchctl describe node ${esc(name)}</code>`]);
  if (body) kv[kv.findIndex(r => r[0] === 'BMC')] = ['BMC', body.bmc_ip ? `<code>${esc(body.bmc_ip)}:443</code> (HTTPS), user admin, password in <code>compose/generated/secrets/bmc_credentials</code>` : 'none for this node (switched off in Cluster architecture)'];
  const row = JSON.parse(S.lastCluster || '{"nodes":[]}').nodes.find(x => x.name === name);
  $('nodesum').innerHTML = nodeSummary(row, body).map(r => `<dt>${esc(r.k)}</dt><dd>${r.code ? `<code>${esc(r.v)}</code>` : esc(r.v)}</dd>`).join('');
  const reach = cx && row && row.kind !== 'k8s' ? cx.command.replace('<node>', name) : '';
  $('nodereach').hidden = !reach; $('nodereach-cmd').textContent = reach;
  $('nodereach-copy').onclick = async () => { try { await navigator.clipboard.writeText(reach); $('nodereach-copy').textContent = 'Copied'; } catch (e) { $('nodereach-copy').textContent = 'Select and copy'; } };
  $('nodeconn').innerHTML = kv.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join(''); bindCopy();
  $('nodetext').textContent = body ? body.text : `Could not read ${name}: ${(await r.json()).detail}`;
}
export async function nodeJob(name, gpus, dur) {
  const r = await fetch(`/api/nodes/${enc(name)}/workload?gpus=${gpus}&duration=${dur}`, { method: 'POST', headers: H });
  const m = r.ok ? (await r.json()).message : `Could not submit on ${name}: ${(await r.json()).detail}`;
  toast(m); $('nodeactmsg').textContent = m;
}
export async function nodeAction(name, action, label) {
  const r = await fetch(`/api/nodes/${enc(name)}/${action}`, { method: 'POST', headers: H });
  if (!r.ok) { const m = `${label} on ${name} was refused: ${(await r.json()).detail}`; toast(m); $('nodeactmsg').textContent = m; return; }
  const res = (await r.json()).results;
  const bad = res.filter(x => x.status === 'failed');
  const skipped = res.filter(x => x.target === 'slurm' && x.status === 'skipped');
  const msg = skipped.length ? `${label} on ${name}: done, but Slurm was not told (${skipped[0].detail}); run ./scripts/benchctl set node ${name} ${action === 'power-off' ? 'off' : action === 'power-on' ? 'on' : 'resume'}` : bad.length ? `${label} on ${name}: ${bad.length} step(s) failed — is the stack running?` : `${label} on ${name}: done`;
  toast(msg); $('nodeactmsg').textContent = msg;
  S.slurmAt = 0;  // read the node states now instead of at the next poll (issue 414)
  S.lastCluster = ''; loadCluster();
}

export function init() {
  $('node-back').onclick = closeNode;
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && S.openNode && !document.querySelector('dialog[open]:not(.drawer)')) closeNode(); });

}
