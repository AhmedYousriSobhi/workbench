// The Traffic analysis screen (redesign step 5b): request river, pocket probe, rhythm, what looks odd, and the focused wire.
// Data: /api/traffic every 3 s, only while the Traffic tab and the page are visible (W-11). All numbers come from trafficanalysis.js;
// what cannot be measured says "not measured", never 0. Drawn into #tan on the Traffic tab, always on a dark canvas.
import { S } from './state.js';
import { $, announce, esc } from './util.js';
import { sv } from './svg.js';
import { buildWire, updateWire } from './wire.js';
import { clusterNow } from './inspector.js';
import { clusterList, clusterTitle } from './overview.js';
import { PROBE_MODES, bandPx, detectPeriods, findAnomalies, parseWireHash, perSecond, probeReading, rateFromSamples, riverModel, sampleOf, streamKey, wireFacts, wireHash, windowRecent } from './trafficanalysis.js';

const POLL_MS = 3000, KEEP_S = 3600, KEEP_ROWS = 3000, MAX_CHIPS = 10, MAX_PERIOD_STREAMS = 12;
const PLANE_OF = { 'bmc-farm': 'management', 'bcm-mock': 'access', 'jira-mock': 'data' };
const SERVICE_NAME = { 'bmc-farm': 'BMC farm', 'bcm-mock': 'BCM', 'jira-mock': 'Jira' };
const OUTCOMES = [['ok', '2xx · answered', 'monitoring'], ['slow', 'slower than p95', 'management'], ['client_error', '4xx · refused', 'management'], ['server_error', '5xx · errors', 'control']];
const A = { timer: 0, samples: [], streams: [], recent: [], edges: [], services: {}, now: 0, since: 0, spot: null, mode: 'Rate', windowS: 300, periods: {}, failed: false, built: '', river: {} };

// ---- data --------------------------------------------------------------------------------------------------------------
function ingest(j) {
  A.now = j.now; A.edges = j.edges || A.edges; A.services = j.services; A.streams = j.streams;
  A.samples.push(sampleOf(j.now, j.streams));
  A.samples = A.samples.filter(s => s.t > j.now - KEEP_S).slice(-1200);
  A.recent = [...A.recent, ...j.recent].filter(r => r[0] > j.now - KEEP_S).slice(-KEEP_ROWS);
  A.since = A.recent.length ? A.recent[A.recent.length - 1][0] : A.since;
  A.periods = {};
  const rows = windowRecent(A.recent, A.now, A.windowS), by = new Map();
  for (const r of rows) { const k = `${r[1]}|${r[2]}|${r[3]}`; (by.get(k) || by.set(k, []).get(k)).push(r[0]); }
  for (const [k, ts] of [...by].sort((a, b) => b[1].length - a[1].length).slice(0, MAX_PERIOD_STREAMS)) { const p = detectPeriods(ts); if (p.length) A.periods[k] = p; }
}
async function pollTraffic() {
  try { ingest(await (await fetch(`/api/traffic?since=${A.since}`)).json()); A.failed = false; } catch (e) { A.failed = true; }
  paintAnalysis();
}
export function startAnalysis() { if (A.timer) return; pollTraffic(); A.timer = setInterval(pollTraffic, POLL_MS); }
export function stopAnalysis() { clearInterval(A.timer); A.timer = 0; }

const rates = () => { const w = Number.isFinite(A.windowS) ? Math.min(A.windowS, 300) : KEEP_S; return Object.fromEntries(A.streams.map(s => [streamKey(s), rateFromSamples(A.samples, streamKey(s), A.now, w)])); };
const label = s => `${s.who} → ${SERVICE_NAME[s.service] || s.service} ${s.shape}`;
const fmt = r => r === null || r === undefined ? 'not measured' : `${Math.round(r * 10) / 10} req/min`;
const chosen = () => A.streams.find(s => streamKey(s) === A.spot) || [...A.streams].sort((a, b) => b.n - a.n)[0] || null;

// ---- the river ---------------------------------------------------------------------------------------------------------
const COL = { a: 190, b: 550, c: 910 }, NW = 230, ROW = 62, PAD = 36;
const bandD = (x1, y1, x2, y2) => { const m = (x1 + x2) / 2; return `M${x1} ${y1} C${m} ${y1} ${m} ${y2} ${x2} ${y2}`; };
const keepStreams = () => A.spot ? A.streams.filter(s => streamKey(s) === A.spot) : A.streams;

function buildRiver(model) {
  const svg = $('an-river'), callers = [...new Set(A.streams.map(s => s.who))].sort(), services = Object.keys(PLANE_OF), H = PAD * 2 + Math.max(callers.length, services.length, OUTCOMES.length) * ROW;
  svg.setAttribute('viewBox', `0 0 1100 ${H}`); svg.replaceChildren(); A.river = { bands: {}, nodes: {} };
  const bands = sv('g', {}), nodes = sv('g', {}), y = (i, n) => PAD + (H - PAD * 2) / n * (i + 0.5);
  const node = (id, x, i, n, title) => {
    const g = sv('g', { class: 'an-node' }), cy = y(i, n);
    g.append(sv('rect', { x: x - NW / 2, y: cy - 24, width: NW, height: 48, rx: 10 }), sv('text', { class: 'an-nt', x, y: cy - 4, 'text-anchor': 'middle' }, title), sv('text', { class: 'an-ns', x, y: cy + 16, 'text-anchor': 'middle' }, ''));
    nodes.append(g); A.river.nodes[id] = { g, cy, sub: g.lastChild }; return cy;
  };
  callers.forEach((c, i) => node('c:' + c, COL.a, i, callers.length, c === 'unknown' ? 'unknown caller' : c));
  services.forEach((s, i) => node('s:' + s, COL.b, i, services.length, SERVICE_NAME[s]));
  OUTCOMES.forEach(([k, t], i) => node('o:' + k, COL.c, i, OUTCOMES.length, t));
  const band = (id, from, to, plane, kind) => {
    const a = A.river.nodes[from], b = A.river.nodes[to], d = bandD(from[0] === 'c' ? COL.a + NW / 2 : COL.b + NW / 2, a.cy, to[0] === 's' ? COL.b - NW / 2 : COL.c - NW / 2, b.cy);
    const g = sv('g', { class: 'an-bandg', 'data-k': kind || plane }), under = sv('path', { class: 'an-band', d, 'data-plane': plane, 'data-k': kind || plane });
    const wire = buildWire({ d, plane, rpm: 0, errors: 0, label: id }); g.append(under, wire); bands.append(g);
    A.river.bands[id] = { under, wire, d, plane, kind };
  };
  for (const c of callers) for (const s of services) if (A.streams.some(x => x.who === c && x.service === s)) band(`c:${c}>s:${s}`, 'c:' + c, 's:' + s, PLANE_OF[s]);
  for (const s of services) for (const [k, , plane] of OUTCOMES) band(`s:${s}>o:${k}`, 's:' + s, 'o:' + k, plane, k);
  svg.append(bands, nodes);
}
function paintRiver() {
  const model = riverModel(keepStreams(), A.edges, rates()), sig = JSON.stringify([A.streams.map(s => `${s.who}|${s.service}`).sort(), A.spot]);
  if (A.built !== sig) { A.built = sig; buildRiver(model); }
  const maxRpm = Math.max(0, ...model.calls.map(c => c.rpm), ...Object.values(model.answers), ...Object.values(model.outcome));
  const byCall = Object.fromEntries(model.calls.map(c => [`c:${c.who}>s:${c.service}`, c.rpm]));
  const out = {};  // service -> outcome -> rpm
  for (const r of model.rows) { if (r.rpm === null) continue; for (const [k] of OUTCOMES) { (out[r.service] ||= {})[k] = ((out[r.service] || {})[k] || 0) + r.rpm * r.share[k]; } }
  for (const [id, b] of Object.entries(A.river.bands)) {
    const rpm = b.kind ? (out[id.slice(2, id.indexOf('>'))] || {})[b.kind] || 0 : byCall[id] || 0;
    b.under.style.strokeWidth = `${rpm > 0 ? Math.max(2, bandPx(rpm, maxRpm, 34)) : 0}px`;
    updateWire(b.wire, { d: b.d, plane: b.plane, rpm, errors: b.kind === 'server_error' && rpm > 0 ? 1 : 0, label: `${id.replace(/[cso]:/g, '')} ${Math.round(rpm * 10) / 10} requests a minute` });
  }
  const sub = (id, rpm) => { const n = A.river.nodes[id]; if (n) n.sub.textContent = model.measured ? fmt(rpm) : 'not measured'; };
  const callerRpm = {}; for (const c of model.calls) callerRpm[c.who] = (callerRpm[c.who] || 0) + c.rpm;
  for (const c of Object.keys(callerRpm)) sub('c:' + c, callerRpm[c]);
  for (const s of Object.keys(PLANE_OF)) sub('s:' + s, model.answers[s] ?? (A.services[s] ? 0 : null));
  for (const [k] of OUTCOMES) sub('o:' + k, model.outcome[k]);
  $('an-river-note').textContent = model.measured ? `Slower than p95: more than ${model.p95 ? (model.p95.ms === Infinity ? 'the slowest bucket' : model.p95.ms + ' ms') : '…'} (taken from the latency buckets).` : 'Waiting for two readings 5 seconds apart: a rate is never shown before it can be measured.';
  $('an-river-text').innerHTML = `<caption>Requests a minute by caller, service and path</caption><tr><th>Caller</th><th>Service</th><th>Path</th><th>Rate</th><th>Answered</th><th>Errors</th></tr>` + model.rows.map(r => `<tr><td>${esc(r.who)}</td><td>${esc(SERVICE_NAME[r.service] || r.service)}</td><td>${esc(r.shape)}</td><td>${fmt(r.rpm)}</td><td>${Math.round(r.share.ok * 100)} %</td><td>${Math.round((r.share.client_error + r.share.server_error) * 100)} %</td></tr>`).join('');
}
function paintStreamChips() {
  const top = [...A.streams].sort((a, b) => b.n - a.n).slice(0, MAX_CHIPS), more = A.streams.length - top.length;
  $('an-chips').innerHTML = `<button type="button" class="btn small" data-spot="" aria-pressed="${!A.spot}">All streams</button>` + top.map(s => `<button type="button" class="btn small" data-spot="${esc(streamKey(s))}" aria-pressed="${A.spot === streamKey(s)}">${esc(label(s))}</button>`).join('') + (more > 0 ? `<span class="dim small">+ ${more} more</span>` : '');
}

// ---- probe, found, rhythm ----------------------------------------------------------------------------------------------
function paintProbe() {
  const s = chosen(), host = $('an-probe-body');
  if (!s) { host.innerHTML = '<p class="dim">No client request recorded yet. Run a client against the stack, or open a page of it from another tool.</p>'; return; }
  const key = streamKey(s), rpm = rates()[key] ?? null, periods = A.periods[key] || [], callers = [...new Set(A.streams.filter(x => x.shape === s.shape && x.service === s.service).map(x => x.who))];
  const r = probeReading(A.mode, s, { rpm, edges: A.edges, periods, callers }), trace = perSecond(A.recent, key, A.now, 120), top = Math.max(1, ...trace);
  const pts = trace.map((v, i) => `${(i / 119 * 300).toFixed(1)},${(36 - v / top * 32).toFixed(1)}`).join(' ');
  host.innerHTML = `<p class="an-pname">${esc(label(s))}</p><div class="an-modes" role="group" aria-label="Reading">${PROBE_MODES.map(m => `<button type="button" class="btn small" data-mode="${m}" aria-pressed="${A.mode === m}">${m}</button>`).join('')}</div>
    <p class="an-big mono" aria-live="polite">${r.big === null ? 'not measured' : esc(r.big === Infinity ? '∞' : r.big)}<span class="an-unit"> ${esc(r.unit)}</span></p>
    <svg class="an-trace" viewBox="0 0 300 40" role="img" aria-label="Requests a second over the last two minutes"><polyline points="${pts}"/></svg><p class="an-verdict">${esc(r.verdict)}</p>`;
}
function paintFound() {
  const f = findAnomalies(windowRecent(A.recent, A.now, A.windowS), A.periods), host = $('an-found-list');
  host.innerHTML = f.length ? f.map(x => `<li class="an-f ${x.kind}"><b aria-hidden="true">▲</b><span><strong>${esc(x.text)}</strong><small>${esc(x.detail)}</small></span></li>`).join('') : `<li class="an-f none"><b aria-hidden="true">✓</b><span><strong>Nothing odd in this window</strong><small>${A.recent.length ? 'Checked the requests recorded so far.' : 'No requests recorded yet.'}</small></span></li>`;
}
function paintRhythm() {
  const cols = Object.entries(A.periods).flatMap(([k, ps]) => ps.map(p => ({ k, ...p, who: k.split('|')[0], shape: k.split('|')[2] }))).sort((a, b) => b.strength - a.strength).slice(0, 8);
  $('an-rhythm-body').innerHTML = cols.length ? cols.map(c => `<div class="an-col${c.who === 'unknown' ? ' unk' : ''}"><i class="an-beat" style="--p:${Math.max(0.4, Math.min(c.seconds, 10))}s" aria-hidden="true"></i><b class="mono">${c.seconds} s</b><span>${esc(c.who === 'unknown' ? 'unidentified caller' : c.who)}</span><small>${c.who === 'unknown' ? '▲ no one claims this timer · ' : ''}${esc(c.shape)}</small></div>`).join('')
    : '<p class="dim">No steady repeat found yet. A period needs at least eight requests and three repeats in the window.</p>';
}

// ---- the focused wire --------------------------------------------------------------------------------------------------
export function currentWire() { const c = clusterNow(); return parseWireHash(location.hash, clusterList(), (c.nodes || []).map(n => n.name)); }
function paintFocus() {
  const host = $('an-focus'), w = currentWire();
  host.hidden = !w;
  if (!w) return;
  const node = (clusterNow().nodes || []).find(n => n.name === w.node), f = wireFacts(node), planeName = { management: 'Management', control: 'Control', k8s: 'Kubernetes control' };
  host.innerHTML = `<h4>Focused wire <small class="dim">from the infrastructure</small></h4><p class="an-path mono">${esc(w.cluster || clusterTitle())} › ${esc(w.node)}</p>
    <p>${node ? `${esc(node.name)} is ${esc(node.label)}.` : 'This node is not in the cluster.'} What is known of its wires:</p>
    <ul class="an-links">${(f ? f.links : []).map(l => `<li><span class="plate" data-plane="${l.plane === 'k8s' ? 'k8s' : l.plane}" aria-hidden="true"></span><b>${esc(l.to)}</b> · ${esc(l.what)} <small class="dim">(${planeName[l.plane]}, declared wiring: counted per service, not per node)</small></li>`).join('')}</ul>
    <p class="an-capture">Messages: ${esc(f ? f.capture : '')}</p>
    <div class="row"><button type="button" class="btn small" id="an-back">‹ Back to the pool</button><button type="button" class="btn small" id="an-clear">Clear focus</button></div>`;
  $('an-back').onclick = () => import('./zoom.js').then(m => { clearWire(); m.zoomTo('pool', { pool: node && node.kind === 'k8s' ? (node.gpus ? 'kg' : 'kc') : node && node.kind !== 'compute' ? 'sg' : 'sc' }); });
  $('an-clear').onclick = clearWire;
}
function clearWire() { history.replaceState(null, '', location.pathname + location.search); paintFocus(); }
export function openWireInTraffic(node) {
  location.hash = wireHash(clusterList().length > 1 ? clusterTitle() : clusterList()[0], node);
}

// ---- the whole screen --------------------------------------------------------------------------------------------------
export function paintAnalysis() {
  if (!$('an-river')) return;
  const svcs = Object.entries(A.services).map(([k, v]) => `${SERVICE_NAME[k] || k}: ${v === true ? 'recording' : v === false ? 'not reachable' : 'no record (older image)'}`).join(' · ');
  $('an-status').textContent = A.failed ? 'The page could not read /api/traffic.' : svcs || 'Waiting for the first reading…';
  $('an-scope').textContent = clusterTitle();
  paintFocus(); paintStreamChips(); paintRiver(); paintProbe(); paintFound(); paintRhythm();
}

export function init() {
  const host = $('tan'); if (!host) return;
  host.innerHTML = `<header class="an-head"><h3 id="an-h">Request analysis</h3><span class="an-scope mono" id="an-scope"></span><span class="grow"></span>
      <label class="small dim" for="an-window">Window</label><select id="an-window" class="btn small"><option value="60">Last minute</option><option value="300" selected>Last 5 minutes</option><option value="all">Since this page opened</option></select></header>
    <p class="an-status small" id="an-status" role="status"></p><div class="an-focus" id="an-focus" hidden></div>
    <section class="an-river" aria-labelledby="an-river-h"><h4 id="an-river-h">Request river</h4><p class="dim small">Where the requests go, and how they end. Band width follows the square root of requests a minute; moving dots are live requests; red is an error answer. Pick a stream to look at it alone.</p>
      <div class="an-chips" id="an-chips" role="group" aria-label="Streams"></div><svg id="an-river" class="an-svg" role="img" aria-label="Request river: callers, the services that answer, and how the requests ended"></svg>
      <p class="dim small" id="an-river-note"></p><table class="srtable" id="an-river-text"></table></section>
    <div class="an-two"><section class="an-probe" aria-labelledby="an-probe-h"><h4 id="an-probe-h">Probe</h4><div id="an-probe-body"></div></section>
      <section class="an-found" aria-labelledby="an-found-h"><h4 id="an-found-h">Found in this window</h4><ul id="an-found-list"></ul></section></div>
    <section class="an-rhythm" aria-labelledby="an-rhythm-h"><h4 id="an-rhythm-h">Rhythm: the timers behind the traffic</h4><p class="dim small">Each column is a repeating period found in one stream's requests, beating at its own pace. A timer no caller claims is the interesting one.</p><div class="an-cols" id="an-rhythm-body"></div></section>`;
  host.addEventListener('click', e => {
    const sp = e.target.closest('[data-spot]'), md = e.target.closest('[data-mode]');
    if (sp) { A.spot = sp.dataset.spot || null; paintAnalysis(); announce(A.spot ? `Showing one stream: ${A.spot.replace(/\|/g, ' ')}` : 'Showing all streams'); }
    if (md) { A.mode = md.dataset.mode; paintProbe(); }
  });
  $('an-window').onchange = e => { A.windowS = e.target.value === 'all' ? Infinity : Number(e.target.value); paintAnalysis(); };
  window.addEventListener('hashchange', () => { if (currentWire()) import('./shell.js').then(m => m.selectTab('traffic', false)); paintFocus(); });
}
