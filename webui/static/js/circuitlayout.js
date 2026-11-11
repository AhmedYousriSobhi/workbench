// The Cluster level of the infrastructure zoom (redesign step 3): where every part and wire sits. Pure data in, plain objects
// out, so it can be tested without a browser (webui/tests/js/circuitlayout.test.mjs); circuit.js draws it.
//
// One fixed architecture (W-17): every cluster gets the same parts in the same places. A part the cluster lacks keeps its
// place with `present: false`, and the drawing greys it. Nothing here reads the page or the network.

export const CV_W = 1700, CV_H = 1010;

// The layer stack beside the drawing: the five planes of the documented links (webui/comms.py). Kubernetes control wires
// are drawn in their own colour but belong to the Control layer.
export const LAYERS = [['all', 'All layers'], ['access', 'Access'], ['control', 'Control'], ['data', 'Data'], ['monitoring', 'Monitoring'], ['management', 'Management']];
export const LAYER_OF = { access: 'access', control: 'control', k8s: 'control', data: 'data', monitoring: 'monitoring', management: 'management' };
export const LAYER_TEXT = {
  all: 'Every wire at once. Pick a layer to isolate its current; the others fade to 10 %.',
  access: 'How the client gets in: SSH to the login node, Teleport, admin SSH to the controller when there is one, and the HTTP APIs.',
  control: 'Slurm: login to slurmctld to every node. Kubernetes: kubectl to the API server, KWOK reporting node status.',
  data: 'Accounting into slurmdbd, and the API server into etcd. Shared storage would join here; it is planned, so its trace stays dark.',
  monitoring: 'The client scrapes the GPU exporter and queries Prometheus; Grafana reads Prometheus.',
  management: 'Out-of-band hardware and tickets: Redfish to the BMC farm, cmsh to BCM, Jira.',
};

const WORST = { fault: 4, down: 3, warn: 2, unknown: 1, ok: 0 };
const worst = (a, b) => (WORST[b] ?? 1) > (WORST[a] ?? 1) ? b : a;

// One square per node up to 64; above that one per 8 nodes, coloured by its worst node (a count is in the title).
export const SQUARE_MAX = 64, PER_GROUP = 8;
export function rackSquares(nodes) {
  if (nodes.length <= SQUARE_MAX) return { per: 1, squares: nodes.map(n => ({ names: [n.name], state: n.state, label: n.label })) };
  const squares = [];
  for (let i = 0; i < nodes.length; i += PER_GROUP) {
    const g = nodes.slice(i, i + PER_GROUP);
    squares.push({ names: g.map(n => n.name), state: g.reduce((s, n) => worst(s, n.state), 'ok'), label: `${g.filter(n => n.state !== 'ok').length} of ${g.length} need attention` });
  }
  return { per: PER_GROUP, squares };
}

// The facts the layout needs, from a cluster snapshot (the page's scoped() view of it), as plain values.
export function circuitFacts(c, { dbd = false, monitoring = false, grafana = false } = {}) {
  const nodes = c.nodes || [], logins = c.logins || [], services = c.services || {};
  const slurmNodes = nodes.filter(n => n.kind !== 'k8s'), k8sNodes = nodes.filter(n => n.kind === 'k8s');
  const sLogin = logins.find(l => (l.kind || 'both') !== 'k8s'), kLogin = logins.find(l => (l.kind || 'both') !== 'slurm');
  return {
    slurm: slurmNodes.length > 0 || !!sLogin, k8s: k8sNodes.length > 0 || 'k8s-api' in services,
    ctl: 'controller' in services, jump: 'jump' in services, tele: 'teleport' in services, dbd, monitoring, grafana,
    sLogin: sLogin || null, kLogin: kLogin || null, kViaSlurmLogin: !!kLogin && kLogin === sLogin,
    pools: {
      sc: slurmNodes.filter(n => n.kind === 'compute'), sg: slurmNodes.filter(n => n.kind === 'gpu' || n.kind === 'real-gpu'),
      kc: k8sNodes.filter(n => !n.gpus), kg: k8sNodes.filter(n => n.gpus > 0),
    },
    services,
  };
}

export const POOLS = { sc: ['CPU pool', 'Slurm'], sg: ['GPU pool', 'Slurm'], kc: ['CPU pool', 'Kubernetes'], kg: ['GPU pool', 'Kubernetes'] };

const box = (id, x, y, w, h, extra) => ({ id, x, y, w, h, ...extra });
const R = (p, dy = 0) => ({ x: p.x + p.w, y: p.y + p.h / 2 + dy }), Lf = (p, dy = 0) => ({ x: p.x, y: p.y + p.h / 2 + dy });
const Tp = (p, dx = 0) => ({ x: p.x + p.w / 2 + dx, y: p.y }), Bt = (p, dx = 0) => ({ x: p.x + p.w / 2 + dx, y: p.y + p.h });
export const ortho = pts => 'M' + pts.map(p => `${Math.round(p.x)} ${Math.round(p.y)}`).join(' L');

// Extra services after the fixed ones: the opt-in real parts the cluster has.
const OPTIONAL_SERVICES = [['jira-real', 'Jira DC', 'real · Data Center', 'management'], ['dcgm-exporter', 'DCGM (real)', 'real · first GPU node', 'monitoring'], ['real-gpu', 'GPU card', 'real · this machine', 'monitoring']];

// The whole drawing: `parts` (boxes and racks), `wires` (each between two parts, with the link it stands for by name), `bands`.
export function circuitSpec(f) {
  const slurm = f.slurm, k8s = f.k8s, parts = {}, add = p => (parts[p.id] = p);
  const col = { clients: 24, access: 310, control: 640, pools: 1000, opt: 1400 };
  add(box('clients', col.clients, 70, 200, 700, { role: 'clients', present: true }));
  add(box('login', col.access, 80, 220, 76, { role: 'login', present: slurm && !!f.sLogin, login: f.sLogin, head: !f.ctl }));
  add(box('teleport', col.access, 190, 220, 76, { role: 'teleport', present: f.tele }));
  add(box('jump', col.access, 300, 220, 76, { role: 'jump', present: f.jump }));
  add(box('slurmctld', col.control, 80, 250, 76, { role: 'slurmctld', present: slurm, ctl: f.ctl }));
  add(box('slurmdbd', col.control, 210, 250, 76, { role: 'slurmdbd', present: slurm && f.dbd }));
  add(box('rack-sc', col.pools, 76, 320, 150, { role: 'rack', pool: 'sc', present: f.pools.sc.length > 0, lane: 'slurm' }));
  add(box('rack-sg', col.pools, 246, 320, 150, { role: 'rack', pool: 'sg', present: f.pools.sg.length > 0, lane: 'slurm' }));
  add(box('storage', col.opt, 210, 220, 76, { role: 'storage', present: false, planned: true }));
  add(box('klogin', col.access, 470, 220, 76, { role: 'klogin', present: k8s && !!f.kLogin, login: f.kLogin, via: f.kViaSlurmLogin }));
  add(box('k8s-api', col.control, 470, 250, 76, { role: 'k8s-api', present: k8s }));
  add(box('etcd', col.control, 566, 250, 60, { role: 'etcd', present: k8s }));
  add(box('kwok', col.control, 650, 250, 76, { role: 'kwok', present: k8s }));
  add(box('rack-kc', col.pools, 466, 320, 150, { role: 'rack', pool: 'kc', present: f.pools.kc.length > 0, lane: 'k8s' }));
  add(box('rack-kg', col.pools, 636, 320, 150, { role: 'rack', pool: 'kg', present: f.pools.kg.length > 0, lane: 'k8s' }));

  // The services row: what the client calls directly. Prometheus and Grafana sit beside the GPU exporter they read.
  const row = [['slurmrestd', 'slurmrestd', ':6820 HTTP + JWT', 'access', slurm], ['bmc-farm', 'BMC farm', 'Redfish :8443', 'management', 'bmc-farm' in f.services],
    ['bcm-mock', 'BCM', 'cmsh · :8081', 'management', 'bcm-mock' in f.services], ['gpu-exporter', 'GPU exporter', ':9400 DCGM', 'monitoring', 'gpu-exporter' in f.services],
    ['prometheus', 'Prometheus', ':9090', 'monitoring', f.monitoring], ['grafana', 'Grafana', ':3000', 'monitoring', f.grafana], ['jira-mock', 'Jira', ':8080', 'management', 'jira-mock' in f.services],
    ...OPTIONAL_SERVICES.filter(([k]) => k in f.services).map(([k, t, s, pl]) => [k, t, s, pl, true])];
  const n = row.length, gap = 20, w = Math.min(184, Math.floor((1620 - (n - 1) * gap) / n)), x0 = 64, y = 890, h = 84;
  row.forEach(([id, title, sub, plane, present], i) => add(box(id, x0 + i * (w + gap), y, w, h, { role: 'service', title, sub, plane, present: !!present })));

  const P = parts, busY = 850, last = Tp(P[row[n - 1][0]]).x;
  const wires = [];
  const wire = (key, from, to, plane, pts, o = {}) => wires.push({ key, from, to, plane, d: ortho(pts), present: P[from].present && P[to].present, ...o });
  // Slurm lane
  wire('clients-login', 'clients', 'login', 'access', [{ x: 224, y: 118 }, Lf(P.login)], { link: ['clients', 'login'], label: ':22', edge: f.sLogin && `clients>${f.sLogin.name}:ssh` });
  wire('clients-teleport', 'clients', 'teleport', 'access', [{ x: 224, y: 228 }, Lf(P.teleport)], { link: ['clients', 'teleport'], label: ':3080', edge: 'clients>teleport:api' });
  wire('teleport-login', 'teleport', 'login', 'access', [Tp(P.teleport), Bt(P.login)], { link: ['teleport', 'login'], label: 'certs', short: true });
  wire('clients-jump', 'clients', 'jump', 'access', [{ x: 224, y: 338 }, Lf(P.jump)], { link: ['clients', 'jump'], label: ':22', edge: 'clients>jump:ssh' });
  wire('jump-login', 'jump', 'login', 'access', [R(P.jump, -10), { x: 545, y: P.jump.y + 28 }, { x: 545, y: 132 }, R(P.login, 14)], { link: ['jump', 'login'], label: 'ssh -J' });
  wire('login-slurmctld', 'login', 'slurmctld', 'control', [R(P.login, -8), Lf(P.slurmctld, -8)], { link: ['login', 'slurmctld'], label: ':6817' });
  wire('clients-controller', 'clients', 'slurmctld', 'access', [{ x: 224, y: 173 }, { x: 612, y: 173 }, { x: 612, y: 142 }, { x: col.control, y: 142 }], { link: ['clients', 'controller'], label: 'admin SSH :2221', requires: 'ctl', edge: 'clients>controller:ssh' });
  wire('jump-controller', 'jump', 'slurmctld', 'access', [R(P.jump, 10), { x: 585, y: P.jump.y + 48 }, { x: 585, y: 150 }, { x: col.control, y: 150 }], { link: ['jump', 'controller'], label: 'ssh -J', requires: 'ctl' });
  wire('slurmctld-sc', 'slurmctld', 'rack-sc', 'control', [R(P.slurmctld, -8), { x: 950, y: P.slurmctld.y + 30 }, { x: 950, y: R(P['rack-sc']).y }, Lf(P['rack-sc'])], { link: ['slurmctld', 'slurmd'], label: ':6818' });
  wire('slurmctld-sg', 'slurmctld', 'rack-sg', 'control', [R(P.slurmctld, 8), { x: 930, y: P.slurmctld.y + 46 }, { x: 930, y: R(P['rack-sg']).y }, Lf(P['rack-sg'])], { link: ['slurmctld', 'slurmd'] });
  wire('slurmctld-dbd', 'slurmctld', 'slurmdbd', 'data', [Bt(P.slurmctld), Tp(P.slurmdbd)], { link: ['slurmctld', 'slurmdbd'], label: ':6819', short: true });
  wire('sg-storage', 'rack-sg', 'storage', 'data', [R(P['rack-sg']), { x: 1360, y: R(P['rack-sg']).y }, { x: 1360, y: R(P.storage).y }, Lf(P.storage)], { link: ['nodes', 'storage'], label: 'planned', planned: true });
  // Kubernetes lane
  wire('clients-klogin', 'clients', 'klogin', 'access', [{ x: 224, y: 508 }, Lf(P.klogin)], { link: ['clients', 'login'], label: ':22', edge: f.kLogin && `clients>${f.kLogin.name}:ssh` });
  wire('klogin-api', 'klogin', 'k8s-api', 'k8s', [R(P.klogin), Lf(P['k8s-api'])], { link: ['clients', 'k8s-api'], label: ':6443' });
  wire('api-etcd', 'k8s-api', 'etcd', 'data', [Bt(P['k8s-api']), Tp(P.etcd)], { link: ['k8s-api', 'etcd'], label: ':2379', short: true });
  wire('kwok-api', 'kwok', 'k8s-api', 'k8s', [Lf(P.kwok), { x: 600, y: R(P.kwok).y }, { x: 600, y: R(P['k8s-api'], 14).y }, Lf(P['k8s-api'], 14)], { link: ['kwok', 'k8s-api'], label: 'node status', short: true });
  wire('kwok-kc', 'kwok', 'rack-kc', 'k8s', [R(P.kwok, -8), { x: 950, y: R(P.kwok, -8).y }, { x: 950, y: R(P['rack-kc']).y }, Lf(P['rack-kc'])], { link: null });
  wire('kwok-kg', 'kwok', 'rack-kg', 'k8s', [R(P.kwok, 8), { x: 930, y: R(P.kwok, 8).y }, { x: 930, y: R(P['rack-kg']).y }, Lf(P['rack-kg'])], { link: null });
  // The HTTP bus under everything: one trunk from the clients, one drop to each service.
  wires.push({ key: 'bus', from: 'clients', to: 'bus', plane: 'access', d: ortho([{ x: 124, y: 770 }, { x: 124, y: busY }, { x: last, y: busY }]), present: true, label: 'HTTP APIs the client calls directly', bus: true });
  for (const [id, , , plane] of row) {
    const p = P[id], x = Tp(p).x;
    wire('drop-' + id, 'clients', id, plane, [{ x, y: busY }, Tp(p)], { link: id === 'prometheus' || id === 'grafana' ? [id, null] : ['clients', id], drop: true, edge: id === 'slurmrestd' || id === 'prometheus' || id === 'grafana' ? null : `clients>${id}:api` });
  }
  // Read by neighbours: Prometheus reads the GPU exporter, Grafana reads Prometheus.
  const side = (key, from, to, label) => wire(key, from, to, 'monitoring', [Lf(P[from]), R(P[to])], { link: [from, to === 'gpu-exporter' ? null : to], label });
  side('prom-gpu', 'prometheus', 'gpu-exporter', 'scrape');
  side('graf-prom', 'grafana', 'prometheus', 'query');
  for (const w2 of wires) if (w2.requires === 'ctl') w2.present = w2.present && f.ctl;
  const bands = { slurm: { x: 290, y: 50, w: 1370, h: 360, present: slurm, note: 'Not in this cluster: no Slurm' }, k8s: { x: 290, y: 430, w: 1370, h: 370, present: k8s, note: 'Not in this cluster: no Kubernetes' } };
  return { parts, wires, bands, row: row.map(r => r[0]), size: { w: CV_W, h: CV_H } };
}

// Which parts a layer keeps lit: those a wire of that layer touches. 'all' keeps everything.
export function layerParts(spec, layer) {
  if (layer === 'all') return null;
  const on = new Set();
  for (const w of spec.wires) if (LAYER_OF[w.plane] === layer) { on.add(w.from); on.add(w.to); }
  return on;
}
