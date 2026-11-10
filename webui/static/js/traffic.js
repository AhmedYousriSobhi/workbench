import { S } from './state.js';
import { clusterNow } from './inspector.js';
import { $, esc } from './util.js';
import { sv } from './svg.js';

export const trRate = {};  // service -> {t, n, rate}: requests per second from the counter, re-sampled every 3 s
export function rateOf(svc, n) {
  const now = Date.now(), r = trRate[svc] ||= { t: now, n, rate: null, hist: [] };
  if (now - r.t >= 3000) { r.rate = Math.max(n - r.n, 0) / ((now - r.t) / 1000); r.t = now; r.n = n; r.hist.push(r.rate); if (r.hist.length > 40) r.hist.shift(); }
  return r.rate;
}
// every link on the chain through `id`: upstream until a source, downstream until a sink
export function pathOf(links, id) {
  const l0 = links.find(l => l.id === id), seen = new Set([id]);
  if (!l0) return seen;
  const walk = (key, other, from) => { for (const l of links) if (l[key] === from && !seen.has(l.id)) { seen.add(l.id); walk(key, other, l[other]); } };
  walk('to', 'from', l0.from); walk('from', 'to', l0.to);
  return seen;
}
export const SLURM_OUT = /^(drain|drained|draining|down|fail|failing|maint|unknown|future)/;
export function nodeHealth() {
  const out = {}, rows = clusterNow().nodes || [];
  for (const n of rows) {
    const part = n.kind === 'k8s' ? 'kwok' : 'nodes', h = out[part] ||= { total: 0, out: 0, warn: 0, why: {} }, st = S.slurmNodes && n.kind !== 'k8s' ? S.slurmNodes[n.name] : null;
    h.total++;
    const why = n.state === 'down' || n.state === 'fault' ? n.label : st && SLURM_OUT.test(st) ? 'Slurm: ' + st : null;
    if (why) { h.out++; h.why[n.name] = why; } else if (n.state === 'warn') { h.warn++; h.why[n.name] = n.label; }
  }
  if (out.nodes) out.slurmd = out.nodes;  // slurmd runs on every Slurm node
  return out;
}
export const partHealth = id => nodeHealth()[id] || null;
export function noteNodeChanges(h) {  // a change in which nodes are out is a timeline event, so a drain shows up without a reload
  const now = {}; for (const part of ['nodes', 'kwok']) for (const [n, w] of Object.entries(h[part]?.why || {})) now[n] = w;
  if (S.trPrevOut) {
    for (const n of new Set([...Object.keys(now), ...Object.keys(S.trPrevOut)])) if (now[n] !== S.trPrevOut[n])
      S.trHist.push({ t: Date.now(), from: 'state', to: 'nodes', label: now[n] ? `${n}: ${now[n]}` : `${n}: back to normal`, nodes: [n], n: 0 });
  }
  S.trPrevOut = now;
}
export function trView(l) {
  const key = l.service || l.to, snap = S.flowSnap, mon = l.targets != null, probe = l.probe != null;
  const up = probe ? l.probe : mon ? l.up === l.targets : snap?.reachable[key];
  const n = l.measured && !mon ? snap?.totals[l.service] : null, rate = n == null ? null : rateOf(l.service, n);
  const ms = mon ? l.scrape_ms : up ? snap?.latency?.[key] : null;
  const ph = !mon && !probe ? (partHealth(l.to) || partHealth(l.from)) : null, part = up !== false && ph && (ph.out || ph.warn) ? (ph.out ? `▲ up, ${ph.out} of ${ph.total} nodes out` : `▲ up, ${ph.warn} of ${ph.total} nodes degraded`) : null;
  return { up, n, rate, ms, mon, state: part || (up === undefined ? '–' : up ? '✓ up' : mon ? `✗ ${l.up} of ${l.targets} up` : '✗ not reachable'),
    ev: l.measured || probe ? 'observed' : 'configured', sw: l.real ? 'real' : 'simulated' };
}
// the same links as a diagram: parts grouped by role in a fixed template (a group is drawn only when it has a part), one line per
// link; the plane is the line's colour and dash pattern (the table names it), ✓/✗ says whether a part answers
export const PLANE_STYLE = { control: ['#4cc9ff', '7 4'], data: ['#3fb950', ''], monitoring: ['#b38cff', '2 4'], management: ['#ffb02e', '10 3 2 3'], access: ['#8da2ff', ''] };
export const TR_ICON = { server: 'M2 3h12v4H2zM2 9h12v4H2zM4.5 5h.01M4.5 11h.01', db: 'M3 4c0-1.1 2.2-2 5-2s5 .9 5 2-2.2 2-5 2-5-.9-5-2zM3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2',
  gear: 'M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4',
  screen: 'M2 3h12v8H2zM6 14h4M8 11v3', cube: 'M8 1.5l6 3v7l-6 3-6-3v-7zM2 4.5l6 3 6-3M8 7.5v7', chart: 'M2 13V3M2 13h12M5 10l3-3 2 2 3-4', lock: 'M4 7h8v6H4zM5.5 7V5a2.5 2.5 0 0 1 5 0v2' };
// what each part is, in one sentence (documentation text; shown when a part is clicked)
export const PART_INFO = {
  'clients': 'The monitoring client, Prometheus, scripts and people who call the cluster from outside.',
  'login': 'The Slurm login node: where users and tools ssh in, submit jobs and run Slurm commands.',
  'jump': 'The jump host: a bare sshd bastion with nothing else installed. Use ssh -J through it to reach a login node or the controller.',
  'controller': 'The controller node: where slurmctld runs. Admins (a whitelist of users and groups) ssh in; it is the BCM head node, so cmsh lives here instead of on the login nodes.',
  'teleport': 'The Teleport proxy: ssh access through one audited front door instead of direct ssh.',
  'slurmrestd': "Slurm's REST API daemon. Clients send HTTP requests with a JWT and it hands them to slurmctld. It sits in Access because it is an entry point for callers, not part of the scheduler.",
  'slurmctld': 'The Slurm controller: schedules jobs, tracks every node and answers all Slurm commands and API calls.',
  'slurmdbd': 'The Slurm database daemon: stores accounting records (jobs, users) in MariaDB for slurmctld and sacct.',
  'slurmd': 'The Slurm node daemon: runs on every compute node, starts jobs when slurmctld asks and reports the node state.',
  'nodes': 'The compute and GPU nodes themselves: simulated containers running sshd and slurmd.',
  'bmc-farm': 'Simulated BMCs: one Redfish endpoint per node for power and sensors.',
  'bcm-mock': 'A mock of the cluster manager (CMDaemon style JSON API) that reports node state.',
  'jira-mock': 'A mock of Jira (a REST subset) that receives tickets and can be told to fail.',
  'jira-real': 'The official Jira Data Center with its own database (optional).',
  'k8s-api': 'The Kubernetes API server: every kubectl call and every control-plane component talks to it.',
  'kube-scheduler': 'Places pods on nodes; it watches the API server.',
  'kube-controller-manager': 'Runs the controllers that keep the cluster as declared (nodes, pods, deployments).',
  'etcd': 'The key-value store where the API server keeps all cluster state.',
  'kwok': 'Creates the fake Kubernetes nodes and their pods; no real kubelet runs.',
  'prometheus': 'Scrapes metrics from the targets on a schedule and stores them.',
  'grafana': 'Dashboards over Prometheus; also receives governor annotations.',
  'gpu-exporter': 'The simulated GPU exporter (DCGM style metrics) that tests can also inject faults into.',
  'real-gpu': 'This machine\'s own NVIDIA card with the real DCGM exporter (optional, needs the driver and container toolkit).',
  'dcgm-exporter': 'The official DCGM exporter on a mock NVML, standing for the first GPU node (optional, about 125 MB).',
  'governor': 'The host memory governor: keeps the machine healthy by pausing or stopping parts under pressure (real).',
  'sim-nodes': 'The simulated per-node metrics targets that Prometheus scrapes.',
  'storage': 'Shared storage for job data. Planned, not built yet.'
};
export const PART_ICON = { jump: 'lock', controller: 'gear', clients: 'screen', login: 'lock', teleport: 'lock', slurmrestd: 'gear', slurmctld: 'gear', slurmdbd: 'db', slurmd: 'server', nodes: 'server', 'bmc-farm': 'server', 'bcm-mock': 'server', 'k8s-api': 'gear',
  'kube-scheduler': 'gear', 'kube-controller-manager': 'gear', etcd: 'db', prometheus: 'chart', grafana: 'chart', 'gpu-exporter': 'chart', 'dcgm-exporter': 'chart', 'real-gpu': 'chart', governor: 'gear', 'sim-nodes': 'server', storage: 'db' };
export const TR_GROUPS = [  // the fixed architecture, two rows by four columns, callers at the left: [title, grid column, grid row, colour, parts]; a part with no link in this cluster is drawn grayed, never dropped
  ['External clients', 0, 0.5, '#4cc9ff', ['clients']],
  ['Access', 1, 0, '#8da2ff', ['jump', 'login', 'teleport', 'slurmrestd']], ['Slurm', 2, 0, '#3fb950', ['controller', 'slurmctld', 'slurmdbd', 'slurmd', 'nodes']], ['Storage', 3, 0, '#c9a0ff', ['storage']],
  ['Management', 1, 1, '#ffb02e', ['bmc-farm', 'bcm-mock', 'jira-mock', 'jira-real']], ['Kubernetes', 2, 1, '#b38cff', ['k8s-api', 'kube-scheduler', 'kube-controller-manager', 'etcd', 'kwok']],
  ['Monitoring', 3, 1, '#4cc9ff', ['prometheus', 'grafana', 'gpu-exporter', 'dcgm-exporter', 'real-gpu', 'governor', 'sim-nodes']]];
export const hhmmss = t => new Date(t).toTimeString().slice(0, 8);
export function paintTrTimeline(links) {
  const now = Date.now(), from = now - S.trRange * 1000, ev = S.trHist.filter(h => h.t >= from), say = h => {
    if (h.from === 'state') return `Node ${h.label}`;
    if (h.from === 'injector') return `Fault ${h.label || 'applied'} on ${(h.nodes || []).join(', ') || h.to}`;
    const l = links.find(k => k.from === h.from && (k.service || k.to) === h.to) || links.find(k => (k.service || k.to) === h.to);
    return `${l ? l.from : h.from} → ${l ? l.to : h.to}${l ? ` (${l.protocol} :${l.port})` : ''}`;
  };
  $('tr-feed').innerHTML = ev.slice(-12).reverse().map(h => `<li><span class="ft">${hhmmss(h.t)}</span><span>${h.from === 'injector' || h.from === 'state' ? '<b class="f" aria-hidden="true">◆</b> ' : ''}${esc(say(h))}</span><span class="dim">${h.from === 'injector' || h.from === 'state' ? '' : h.n + ' req' + (h.ms != null ? ', ' + h.ms + ' ms' : '')}</span></li>`).join('')
    || `<li class="dim"><span></span><span>No requests seen in the last ${S.trRange >= 3600 ? '1 h' : S.trRange / 60 + ' min'}. A count rises only when a client calls a mock service.</span><span></span></li>`;
  const B = 60, w = 480, h = 120, bw = S.trRange * 1000 / B, bins = new Array(B).fill(0), faults = [];
  ev.forEach(e => { const i = Math.min(B - 1, Math.floor((e.t - from) / bw)); if (e.from === 'injector' || e.from === 'state') faults.push(i); else bins[i] += e.n; });
  const max = Math.max(...bins, 1), x = i => (i + 0.5) / B * w, y = v => h - 14 - v / max * (h - 26), pts = bins.map((v, i) => `${x(i)},${y(v)}`).join(' ');
  $('tr-chart').innerHTML = `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Requests over the last ${S.trRange / 60} minutes, peak ${max} per ${Math.round(bw / 1000)} s bucket, ${faults.length} fault${faults.length === 1 ? '' : 's'} marked"><polygon points="0,${h - 14} ${pts} ${w},${h - 14}" fill="var(--accent)" fill-opacity="0.18"/><polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>`
    + faults.map(i => `<path d="M${x(i)} 4V${h - 14}" stroke="#ffb02e" stroke-width="2" stroke-dasharray="3 3"/>`).join('')
    + `<text x="0" y="${h - 2}" font-size="11" fill="currentColor" opacity=".7">${hhmmss(from)}</text><text x="${w}" y="${h - 2}" font-size="11" text-anchor="end" fill="currentColor" opacity=".7">${hhmmss(now)}</text><text x="${w}" y="12" font-size="11" text-anchor="end" fill="currentColor" opacity=".7">peak ${max} per ${Math.round(bw / 1000)} s</text></svg>`;
}
export function saveTrLayout() { try { localStorage.setItem('workbench-tr-layout5', JSON.stringify(S.trOff)); localStorage.setItem('workbench-tr-order1', JSON.stringify(S.trOrd)); } catch (e) { /* not remembered */ } }
// auto layout from the links on show (groups with no link are left out, columns close up); a dragged group keeps its offset; lines are routed from the final positions
export function paintTrafficMap(rows, ghosts = []) {
  S.trRows = rows; S.trGhosts = ghosts;
  const svg = $('tr-map'), links = rows.map(r => r.l), live = new Set(links.flatMap(l => [l.from, l.to])), ids = new Set([...live, ...TR_GROUPS.flatMap(g => g[4])]);
  svg.replaceChildren();
  $('tr-legend').innerHTML = [...new Set(links.map(l => l.plane))].map(p => `<span class="trkey"><i style="background:${PLANE_STYLE[p]?.[0] || '#8da2ff'}"></i>${esc(p)}</span>`).join('') + '<span class="dim">Arrows run from the caller to the part called; moving dots mean the far end answers; the number is the port; grayed: not in this cluster, or not readable here. Drag a group (or use the arrow keys) to move it.</span>';
  $('tr-reset').hidden = !Object.keys(S.trOff).length && !Object.keys(S.trOrd).length;
  const known = new Set(TR_GROUPS.flatMap(g => g[4])), other = [...ids].filter(i => !known.has(i)), groups = [...TR_GROUPS, ...(other.length ? [['Other', 1, 1, '#8da2ff', other]] : [])]
    .map(([title, c, r, col, parts]) => ({ title, c, r, col, parts: parts.filter(p => ids.has(p)).sort((a, b) => (S.trOrd[title]?.indexOf(a) ?? -1) - (S.trOrd[title]?.indexOf(b) ?? -1) || 0) })).filter(g => g.parts.length);
  const GW = 170, PH = 40, HEAD = 30, GAPX = 140, GAPY = 40, M = 30, pos = {};
  groups.forEach(g => { g.h = HEAD + g.parts.length * (PH + 6) + 8; });
  const rowH = {}; groups.filter(g => g.r % 1 === 0).forEach(g => { rowH[g.r] = Math.max(rowH[g.r] || 0, g.h); });
  const rowY = {}; let y = M + 10; Object.keys(rowH).map(Number).sort().forEach(r => { rowY[r] = y; y += rowH[r] + GAPY; });
  const ry = r => rowY[Math.floor(r)] + (r % 1 ? (rowY[Math.ceil(r)] - rowY[Math.floor(r)]) * (r % 1) : 0);  // a fractional row sits between two rows
  const cols = [...new Set(TR_GROUPS.map(g => g[1]))].sort((p, q) => p - q);
  groups.forEach(g => { const o = S.trOff[g.title] || [0, 0]; g.x = Math.max(M / 2, M + cols.indexOf(g.c) * (GW + GAPX) + o[0]); g.y = Math.max(M / 2, ry(g.r) + o[1]); });
  const w = Math.max(...groups.map(g => g.x + GW)) + M + 64,  h = Math.max(...groups.map(g => g.y + g.h)) + M / 2;  // +64: room for the same-group brackets on the right
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`); svg.style.width = S.trZoom * 100 + '%'; svg.style.height = 'auto'; svg.style.minWidth = Math.min(w, 560) + 'px';
  const defs = sv('defs', {}); for (const [p, [c]] of Object.entries(PLANE_STYLE)) { const m = sv('marker', { id: 'tm-' + p, viewBox: '0 0 10 10', refX: 9, refY: 5, markerUnits: 'userSpaceOnUse', markerWidth: 9, markerHeight: 9, orient: 'auto' }); m.append(sv('path', { d: 'M0 0 L10 5 L0 10 z', fill: c })); defs.append(m); }
  svg.append(defs);
  groups.forEach(g => {
    const box = sv('g', { 'data-group': g.title, tabindex: 0, role: 'group', 'aria-label': g.title + ' group, movable', style: 'cursor:grab' });
    box.append(sv('rect', { x: g.x, y: g.y, width: GW, height: g.h, rx: 10, fill: g.col, 'fill-opacity': 0.08, stroke: g.col, 'stroke-opacity': 0.8, 'stroke-width': 1.6 }));
    box.append(sv('path', { d: `M${g.x} ${g.y + 28}V${g.y + 10}a10 10 0 0 1 10-10h${GW - 20}a10 10 0 0 1 10 10v18z`, fill: g.col, 'fill-opacity': 0.22 }));
    box.append(sv('text', { x: g.x + 12, y: g.y + 20, fill: g.col, 'font-size': 15, 'font-weight': 700 }, g.title));
    svg.append(box);
    g.parts.forEach((id, i) => {
      const up = S.flowSnap?.reachable[id], gone = !live.has(id), py = g.y + HEAD + i * (PH + 6), ph = gone ? null : partHealth(id), allOut = ph && ph.out === ph.total, mark = ph && (ph.out || ph.warn) ? (allOut ? '✗ ' : '▲ ') : null;
      pos[id] = { x: g.x + 10, y: py, w: GW - 20, g };
      svg.append(sv('rect', { x: g.x + 10, y: py, width: GW - 20, height: PH, rx: 7, fill: 'var(--panel)', stroke: gone ? 'var(--line)' : g.col, 'stroke-opacity': gone ? 1 : 0.7, 'stroke-width': 1.4, 'stroke-dasharray': up === false || gone || allOut ? '4 3' : '', 'opacity': gone ? 0.4 : 1, 'pointer-events': 'none' }));
      const ic = sv('path', { d: TR_ICON[PART_ICON[id] || 'cube'], transform: `translate(${g.x + 18} ${py + PH / 2 - 8})`, fill: 'none', stroke: g.col, 'stroke-width': 1.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', opacity: gone ? 0.4 : 1, 'pointer-events': 'none' });
      svg.append(ic);
      svg.append(sv('text', { x: g.x + 42, y: py + PH / 2 + 5, fill: 'currentColor', 'font-size': 14, 'opacity': gone ? 0.4 : 1, 'pointer-events': 'none' }, (gone ? '' : up === false ? '✗ ' : mark || (up === undefined ? '' : '✓ ')) + (ph && ph.total ? `${id} ${ph.total - ph.out}/${ph.total}` : id.length > 14 ? id.slice(0, 13) + '…' : id)));
      svg.append(sv('rect', { class: 'trpart', 'data-part': id, 'data-of': g.title, tabindex: 0, role: 'button', 'aria-label': `${id}: click for details, drag up or down or press Alt+Up or Alt+Down to reorder inside ${g.title}`, x: g.x + 10, y: py, width: GW - 20, height: PH, fill: 'transparent', style: 'cursor:ns-resize' }));
    });
  });
  // polyline routing: a short stub out of the box, a straight run to a stub into the target; a run that would cross a group goes along the gap between rows instead
  const lanes = {}, ends = {}, lane = k => { const n = lanes[k] = (lanes[k] || 0) + 1; return ((n - 1) % 8 - 3.5) * 5.5; };
  const end = (id, side) => { const k = id + side, n = ends[k] = (ends[k] || 0) + 1; return (n - 1) % 5 * 8 - 16; };  // spread arrivals on one box edge
  const ST = 14, cross = (p, q, A, B) => groups.some(g => g !== A.g && g !== B.g && Array.from({ length: 25 }, (_, i) => i / 24).some(t => { const x = p[0] + (q[0] - p[0]) * t, y = p[1] + (q[1] - p[1]) * t; return x > g.x - 4 && x < g.x + GW + 4 && y > g.y - 4 && y < g.y + g.h + 4; }));
  const gapAt = (A, B) => { const r = B.g.r, f = Math.floor(r); return r > A.g.r ? rowY[Math.ceil(r)] - GAPY / 2 : r < A.g.r ? rowY[f] + rowH[f] + GAPY / 2 : rowY[f] - GAPY / 2; };  // the gap between rows on the target's side
  const rounded = pts => pts.map((q, i) => {
    if (!i) return `M${q[0]} ${q[1]}`; if (i === pts.length - 1) return `L${q[0]} ${q[1]}`;
    const a = pts[i - 1], c = pts[i + 1], r = 10, u = m => { const d = Math.hypot(m[0] - q[0], m[1] - q[1]) || 1, k = Math.min(r, d / 2) / d; return [q[0] + (m[0] - q[0]) * k, q[1] + (m[1] - q[1]) * k]; };
    const p1 = u(a), p2 = u(c); return `L${p1[0]} ${p1[1]}Q${q[0]} ${q[1]} ${p2[0]} ${p2[1]}`;
  }).join('');
  const pills = [];
  [...ghosts.filter(l => l.why !== 'not in this cluster').map(l => ({ l, ghost: true })), ...links.map(l => ({ l }))].forEach(({ l, ghost }) => {
    const A = pos[l.from], B = pos[l.to];
    if (!A || !B) return;
    const right = B.x > A.x + A.w + 30, left = B.x + B.w < A.x - 30, so = left ? -1 : 1, si = right ? -1 : 1;  // sides: 1 = right edge
    const x1 = so > 0 ? A.x + A.w : A.x, x2 = si > 0 ? B.x + B.w : B.x, y1 = A.y + PH / 2 + end(l.from, 'o' + so), y2 = B.y + PH / 2 + end(l.to, 'i' + si), [c, dash] = PLANE_STYLE[l.plane] || ['#8da2ff', ''];
    let pts;
    // same column: nested brackets on the right
    if (!right && !left) { const bk = 'b' + Math.round(Math.max(x1, x2)), bi = (lanes[bk] = (lanes[bk] || 0) + 1) - 1, out = Math.max(x1, x2) + ST + 8 + bi * 18; pts = [[x1, y1], [out, y1], [out, y2], [x2, y2]]; }  // same column: a bracket on the right
    else {  // flowchart routing: out sideways, one vertical lane in the gap before the target, in sideways; a run that would cross a group goes along the gap between rows
      const gx = (right ? B.g.x - GAPX / 2 : B.g.x + GW + GAPX / 2) + lane('v' + Math.round(B.g.x) + so);
      if (right && cross([x1 + ST, y1], [gx, y1], A, B)) { const yc = gapAt(A, B) + lane('c' + Math.round(gapAt(A, B))), gs = A.g.x + GW + GAPX / 2 + lane('v' + Math.round(A.g.x) + 's'); pts = [[x1, y1], [gs, y1], [gs, yc], [gx, yc], [gx, y2], [x2, y2]]; }
      else pts = [[x1, y1], [gx, y1], [gx, y2], [x2, y2]];
    }
    pts = pts.filter((q, i) => !i || q[0] !== pts[i - 1][0] || q[1] !== pts[i - 1][1]);
    const d = rounded(pts), g = ghost ? sv('g', { opacity: 0.4 }) : sv('g', { 'data-link': l.id, style: 'cursor:pointer' }), sel = l.id === S.trSel, mk = PLANE_STYLE[l.plane] ? l.plane : 'access';
    g.append(sv('path', { d, fill: 'none', stroke: 'var(--panel)', 'stroke-width': ghost ? 0 : 6.5, 'stroke-linejoin': 'round', 'pointer-events': 'none' }));  // a casing, so two lines that run close still read as two
    if (!ghost) g.append(sv('path', { d, fill: 'none', stroke: 'transparent', 'stroke-width': 14 }));
    g.append(sv('path', { d, fill: 'none', stroke: ghost ? 'var(--dim)' : c, 'stroke-width': sel ? 4.5 : ghost ? 1.6 : 2.8, 'stroke-opacity': ghost ? 1 : 0.85, 'stroke-linejoin': 'round', 'stroke-dasharray': ghost ? '2 5' : '', 'marker-end': ghost ? '' : `url(#tm-${mk})` }));
    if (!ghost && trView(l).up === true) g.append(sv('path', { d, class: 'trdots', fill: 'none', stroke: '#fff', 'stroke-opacity': 0.9, 'stroke-width': 2.4, 'stroke-linecap': 'round', 'stroke-dasharray': '0.1 14', 'pointer-events': 'none' }));  // moving dots: the caller-to-callee direction, only on a link whose far end answers
    if (l.port != null && !ghost) pills.push({ l, c, pts, home: [x2 + si * (ST + 18), y2] });
    g.append(sv('title', {}, `${l.from} to ${l.to}, ${l.plane} plane, ${l.protocol}${l.port == null ? '' : ' port ' + l.port}` + (ghost ? ` (grayed: ${l.why})` : '')));
    svg.append(g);
  });
  // port pills last, each on a free spot of its own line: along the line's segments, away from other pills and from the groups
  const taken = [], hit = (a, b, pad) => a.x < b.x + b.w + pad && b.x < a.x + a.w + pad && a.y < b.y + b.h + pad && b.y < a.y + a.h + pad, boxes = groups.map(g => ({ x: g.x, y: g.y, w: GW, h: g.h }));
  const top = sv('g', {}); svg.append(top);
  pills.forEach(({ l, c, pts, home }) => {
    const wd = String(l.port).length * 7 + 14, at = (cx, cy) => ({ x: cx - wd / 2, y: cy - 8, w: wd, h: 16, cx, cy });
    const segs = pts.slice(1).map((q, i) => [pts[i], q]).reverse().filter(([a, b]) => Math.hypot(b[0] - a[0], b[1] - a[1]) >= 30);
    const spots = segs.flatMap(([a, b]) => [0.5, 0.35, 0.65, 0.2, 0.8].map(t => at(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)));
    const r = spots.find(q => !taken.some(o => hit(q, o, 3)) && !boxes.some(o => hit(q, o, 1))) || at(home[0], home[1]);
    taken.push(r);
    const g = sv('g', { 'data-link': l.id, style: 'cursor:pointer' });
    g.append(sv('rect', { x: r.x, y: r.y, width: wd, height: 16, rx: 8, fill: 'var(--panel)', stroke: c, 'stroke-width': 1.4 }), sv('text', { x: r.cx, y: r.cy + 4, 'text-anchor': 'middle', fill: 'currentColor', 'font-size': 11, 'font-weight': 600 }, l.port));
    top.append(g);
  });
}
// the right-hand inspector: one link's facts, a rate chart from the counter samples taken while the tab is open
export function rateChart(hist) {
  if (!hist || hist.length < 2) return '<p class="dim small">The chart fills in as the page samples the counter (every 3 s).</p>';
  const max = Math.max(...hist, 1), pts = hist.map((v, i) => `${(i / (hist.length - 1)) * 240},${46 - (v / max) * 42}`).join(' ');
  return `<svg viewBox="0 0 240 50" width="100%" height="50" role="img" aria-label="Requests per second, newest on the right, peak ${+max.toFixed(1)}"><polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.8"/></svg><div class="dim small">Peak ${+max.toFixed(1)}/s over the last ${hist.length * 3} s</div>`;
}
export function paintTrPart(id, links) {  // a clicked part: what it is, whether it answers, and the links it has (each opens that link)
  const up = S.flowSnap?.reachable[id], mine = links.filter(l => l.from === id || l.to === id), li = l => `<li><button class="linkbtn" data-link="${esc(l.id)}">${l.from === id ? `→ ${esc(l.to)}` : `← ${esc(l.from)}`}</button> <span class="dim small">${esc(l.protocol)}${l.port == null ? '' : ' :' + l.port}</span></li>`;
  $('tr-insp').innerHTML = `<h3>${esc(id)} <button class="btn small" id="tr-close" aria-label="Close the inspector">✕</button></h3><p class="small">${esc(PART_INFO[id] || '')}</p><dl><dt>State</dt><dd>${up === undefined ? '–' : up ? '✓ answers' : '✗ not reachable'}</dd>${(ph => ph && (ph.out || ph.warn) ? `<dt>Nodes</dt><dd>${ph.total - ph.out} of ${ph.total} in service<br>${Object.entries(ph.why).map(([n, w]) => `${esc(n)}: ${esc(w)}`).join('<br>')}</dd>` : ph ? `<dt>Nodes</dt><dd>${ph.total} of ${ph.total} in service</dd>` : '')(partHealth(id))}</dl><p class="row small"><span class="dim">Order in its group</span><button class="btn small" data-mv="-1" aria-label="Move ${esc(id)} up in its group">▲ Up</button><button class="btn small" data-mv="1" aria-label="Move ${esc(id)} down in its group">▼ Down</button></p>`
    + (mine.length ? `<p class="small dim">Links of this part (click one for its details)</p><ul class="trlinks">${mine.map(li).join('')}</ul>` : '<p class="small dim">No documented link in this cluster.</p>');
}
export function paintTrInspector(cur) {
  const box = $('tr-insp');
  if (!cur) { box.innerHTML = '<h3>Traffic flow inspector</h3><p class="dim small">Select a link or a part in the diagram, or a link in the table, to see its details here.</p>'; return; }
  const v = trView(cur), row = (k, val) => `<dt>${k}</dt><dd>${val}</dd>`;
  box.innerHTML = `<h3>Traffic flow inspector <button class="btn small" id="tr-close" aria-label="Close the inspector">✕</button></h3><dl>`
    + row('Source', esc(cur.from)) + row('Destination', esc(cur.to)) + row('Protocol', esc(cur.protocol)) + row('Port', cur.port)
    + row('Direction', `${esc(cur.from)} → ${esc(cur.to)}`) + row('Plane', esc(cur.plane)) + row('Software', v.sw) + row('Evidence', v.ev)
    + row('Request rate', v.rate == null ? 'not counted for this link' : `${+v.rate.toFixed(1)} req/s`)
    + row('Latency', v.ms == null ? 'not measured' : `${v.ms} ms (${v.mon ? 'Prometheus scrape' : 'this page\'s probe'})`)
    + row('Requests', v.mon ? `${cur.targets} target${cur.targets === 1 ? '' : 's'} scraped` : v.n == null ? '–' : v.n.toLocaleString())
    + row('State', v.state) + `</dl><p class="small">${esc(cur.what)}</p>` + rateChart(trRate[cur.service]?.hist)
    + '<button class="btn small" id="tr-follow">Follow the path</button>';
}
export function paintTraffic() {
  if ($('tab-traffic').hidden) return;
  paintSlurmStats();
  const base = clusterNow().comms;
  if (!base) {  // the page is newer than the server that serves it (a server started before an update never sends the links)
    if (S.lastCluster) $('tr-rows').innerHTML = '<tr><td colspan="9" class="skel">The web page server is older than this page, so it sends no links. Restart it: <code>pkill -f \"python -m webui\"; ./scripts/benchctl start</code></td></tr>';
    return;
  }
  noteNodeChanges(nodeHealth());
  const links = [...base, ...S.monLinks, ...(S.dbdLink ? [S.dbdLink] : [])];
  paintChips(links);
  const path = S.trPath && pathOf(links, S.trPath), f = { plane: S.trPlane, sw: $('tr-sw').value, ev: $('tr-ev').value };
  const rows = links.filter(l => !path || path.has(l.id)).map(l => ({ l, v: trView(l) }))
    .filter(({ l, v }) => (f.plane === 'all' || l.plane === f.plane) && (f.sw === 'all' || v.sw === f.sw) && (f.ev === 'all' || v.ev === f.ev));
  $('tr-rows').innerHTML = rows.map(({ l, v }) => `<tr${l.id === S.trSel ? ' aria-selected="true"' : ''}><td><button class="linkbtn" data-link="${esc(l.id)}" aria-pressed="${l.id === S.trSel}">${esc(l.from)}</button></td><td>${esc(l.to)}</td>`
    + `<td>${esc(l.protocol)} :${l.port}<br><span class="dim small">${esc(l.what)}</span></td><td>${esc(l.plane)}</td><td>${v.sw}</td><td>${v.ev}</td>`
    + `<td>${v.mon ? `${l.targets} target${l.targets === 1 ? '' : 's'}` : v.n == null ? '–' : v.n.toLocaleString() + (v.rate == null ? '' : ` (${+v.rate.toFixed(1)}/s)`)}</td>`
    + `<td>${v.ms == null ? '–' : v.ms + ' ms'}</td><td>${v.state}</td></tr>`).join('') || '<tr><td colspan="9" class="skel">No links.</td></tr>';
  paintTrTimeline(links);
  paintTrafficMap(rows, !path && f.plane === 'all' && f.sw === 'all' && f.ev === 'all' ? (clusterNow().absent || []).filter(l => !S.dbdLink || l.id !== S.dbdLink.id) : []);
  const pb = $('tr-path');
  pb.hidden = !path;
  if (path) pb.innerHTML = `Path through <b>${esc(S.trPath)}</b>: ${path.size} link${path.size === 1 ? '' : 's'}. <button class="btn small" id="tr-clear">Show all links</button>`;
  const cur = links.find(l => l.id === S.trSel);
  if (!cur && S.trPart) paintTrPart(S.trPart, links); else paintTrInspector(cur);
}
export function paintChips(links) {
  const planes = ['all', ...new Set(links.map(l => l.plane))], box = $('tr-chips');
  if (!planes.includes(S.trPlane)) S.trPlane = 'all';
  if (box.dataset.k === planes.join()) return;
  box.dataset.k = planes.join();
  box.innerHTML = planes.map(p => `<button class="btn small trchip" data-plane="${esc(p)}" aria-pressed="${p === S.trPlane}">${p === 'all' ? '' : `<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="${PLANE_STYLE[p]?.[0] || 'currentColor'}" stroke-width="1.6" stroke-linecap="round"><path d="${TR_ICON[PLANE_ICON[p] || 'cube']}"/></svg>`}${p === 'all' ? 'All traffic' : esc(p)}</button>`).join('');
}
export const PLANE_ICON = { control: 'gear', data: 'db', monitoring: 'chart', management: 'server', access: 'lock' };
export async function paintSlurmStats() {
  if ($('tab-traffic').hidden || Date.now() - S.slurmAt < 5000) return;
  S.slurmAt = Date.now();
  fetch('/api/monitoring').then(r => r.json()).then(j => { S.monLinks = j.links; paintTraffic(); }).catch(() => { S.monLinks = []; });
  let s = null;
  try { s = (await (await fetch('/api/slurm-stats')).json()).stats; } catch (e) { /* the governor or the stack is down: show nothing */ }
  S.slurmNodes = s && s.node_states ? s.node_states : null;
  S.dbdLink = s && s.dbd != null ? { id: 'slurmctld>slurmdbd:6819', from: 'slurmctld', to: 'slurmdbd', protocol: 'TCP (Slurm RPC)', port: 6819, plane: 'data', what: 'accounting records; seen by a sacctmgr query (answers or not, no counter)', real: true, measured: false, probe: s.dbd, service: null } : null;
  paintTraffic();
  const box = $('tr-slurm');
  box.hidden = !s;
  if (s) box.innerHTML = `<b>slurmctld (sdiag)</b>: ${s.rpcs.toLocaleString()} requests served since it started${s.threads == null ? '' : `, ${s.threads} threads now`}. Busiest: `
    + s.top.map(t => `${esc(t.type)} ${t.count.toLocaleString()} (${t.ave_us.toLocaleString()} µs each)`).join(', ') + '.';
}

export function init() {
  $('tr-range').onclick = e => { const b = e.target.closest('[data-s]'); if (!b) return; S.trRange = +b.dataset.s; $('tr-range').querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b)); paintTraffic(); };
  try { S.trOff = JSON.parse(localStorage.getItem('workbench-tr-layout5') || '{}'); } catch (e) { /* default layout */ }
  try { S.trOrd = JSON.parse(localStorage.getItem('workbench-tr-order1') || '{}'); } catch (e) { /* default order */ }
  // move a group by dragging or the arrow keys; the lines re-route on every move, the offsets are remembered in this browser
  (() => {
    const svg = $('tr-map'); let drag = null;
    const move = (title, dx, dy) => { const o = S.trOff[title] || [0, 0]; S.trOff[title] = [o[0] + dx, o[1] + dy]; paintTrafficMap(S.trRows, S.trGhosts); };
    svg.addEventListener('pointerdown', e => { const g = e.target.closest('[data-group]'); if (!g || e.target.closest('[data-link]') || (e.pointerType === 'touch' && !svg.classList.contains('moving'))) return; drag = { t: g.dataset.group, x: e.clientX, y: e.clientY }; svg.setPointerCapture(e.pointerId); });
    svg.addEventListener('pointermove', e => { if (!drag) return; const k = svg.viewBox.baseVal.width / svg.getBoundingClientRect().width; move(drag.t, (e.clientX - drag.x) * k, (e.clientY - drag.y) * k); drag.x = e.clientX; drag.y = e.clientY; });
    const stop = () => { if (drag) { drag = null; saveTrLayout(); } };
    svg.addEventListener('pointerup', stop); svg.addEventListener('pointercancel', stop);
    svg.addEventListener('keydown', e => {
      const g = e.target.closest?.('[data-group]'), d = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] }[e.key];
      if (!g || !d) return; e.preventDefault(); const t = g.dataset.group; move(t, d[0], d[1]); saveTrLayout(); svg.querySelector(`[data-group="${CSS.escape(t)}"]`)?.focus();
    });
    $('tr-move').onclick = () => { const on = !svg.classList.contains('moving'); svg.classList.toggle('moving', on); $('tr-move').setAttribute('aria-pressed', on); };
    // reorder the parts of a group: drag one up or down inside its group, or Alt+Up / Alt+Down; the order is remembered in this browser
    const order = t => S.trRows.length ? Array.from(svg.querySelectorAll(`[data-part][data-of="${CSS.escape(t)}"]`), r => r.dataset.part) : [];
    const shift = (id, t, to) => { const o = order(t), i = o.indexOf(id), j = Math.max(0, Math.min(o.length - 1, to)); if (i < 0 || i === j) return false; o.splice(j, 0, o.splice(i, 1)[0]); S.trOrd[t] = o; paintTrafficMap(S.trRows, S.trGhosts); return true; };
    let pd = null;
    svg.addEventListener('pointerdown', e => { const r = e.target.closest('[data-part]'); if (!r) return; pd = { id: r.dataset.part, t: r.dataset.of, y: e.clientY, y0: e.clientY, moved: false }; r.classList.add('drag'); svg.setPointerCapture(e.pointerId); });
    svg.addEventListener('pointermove', e => { if (!pd) return; const k = svg.viewBox.baseVal.width / svg.getBoundingClientRect().width, step = ((40 + 6)) / k, i = order(pd.t).indexOf(pd.id), n = Math.round((e.clientY - pd.y) / step); if (Math.abs(e.clientY - pd.y0) > 4 && !pd.moved) { pd.moved = true; svg.classList.add('dragging'); } if (n && shift(pd.id, pd.t, i + n)) { pd.y += n * step; svg.querySelector(`[data-part="${CSS.escape(pd.id)}"]`)?.classList.add('drag'); } });
    const stopPart = () => { svg.classList.remove('dragging'); if (pd) { const { id, moved } = pd; pd = null; if (moved) saveTrLayout(); else { S.trPart = S.trPart === id ? null : id; S.trSel = null; paintTraffic(); } } };
    $('tr-insp').addEventListener('click', e => { const b = e.target.closest('[data-mv]'); const r = b && S.trPart && svg.querySelector(`[data-part="${CSS.escape(S.trPart)}"]`); if (r && shift(S.trPart, r.dataset.of, order(r.dataset.of).indexOf(S.trPart) + +b.dataset.mv)) saveTrLayout(); });  // a button for the same move, for a finger or a mouse that cannot drag
    svg.addEventListener('pointerup', stopPart); svg.addEventListener('pointercancel', stopPart);
    svg.addEventListener('keydown', e => {
      const r = e.target.closest?.('[data-part]'); if (r && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); S.trPart = r.dataset.part; S.trSel = null; paintTraffic(); return; }
      if (!r || !e.altKey || !['ArrowUp', 'ArrowDown'].includes(e.key)) return;
      e.preventDefault(); const id = r.dataset.part, t = r.dataset.of; if (shift(id, t, order(t).indexOf(id) + (e.key === 'ArrowUp' ? -1 : 1))) saveTrLayout();
      svg.querySelector(`[data-part="${CSS.escape(id)}"]`)?.focus();
    });
    $('tr-reset').onclick = () => { S.trOff = {}; S.trOrd = {}; saveTrLayout(); paintTrafficMap(S.trRows, S.trGhosts); };
  })();
  $('tr-rows').onclick = $('tr-map').onclick = $('tr-insp').onclick = e => { const b = e.target.closest('[data-link]'); if (b) { S.trSel = S.trSel === (b.dataset.link) ? null : b.dataset.link; S.trPart = null; paintTraffic(); } };
  document.addEventListener('click', e => {
    const id = e.target.id;
    if (id === 'tr-follow') { S.trPath = S.trSel; paintTraffic(); } else if (id === 'tr-clear') { S.trPath = null; paintTraffic(); } else if (id === 'tr-close') { S.trSel = null; S.trPart = null; paintTraffic(); }
  });
  $('tr-sw').onchange = $('tr-ev').onchange = paintTraffic;
  $('tr-chips').onclick = e => { const b = e.target.closest('[data-plane]'); if (!b) return; S.trPlane = b.dataset.plane; $('tr-chips').dataset.k = ''; paintTraffic(); };
  const zoom = z => { S.trZoom = Math.min(3, Math.max(0.6, z)); $('tr-map').style.width = S.trZoom * 100 + '%'; };
  $('tr-zin').onclick = () => zoom(S.trZoom * 1.25); $('tr-zout').onclick = () => zoom(S.trZoom / 1.25); $('tr-zfit').onclick = () => zoom(1);
  if ($('tr-stage').requestFullscreen) $('tr-full').onclick = () => document.fullscreenElement ? document.exitFullscreen() : $('tr-stage').requestFullscreen(); else $('tr-full').hidden = true;
  setInterval(() => { if (!document.hidden) paintSlurmStats(); }, 5000);  // node states are read every 5 s while the Traffic tab shows (W-11: not while hidden), so a drain appears without traffic or a reload (issue 414)

}
