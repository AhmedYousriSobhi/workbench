// The numbers behind the Traffic analysis screen (redesign step 5): request rates, the river, the pocket probe, repeated-request
// periods and what looks odd. Pure functions over `/api/traffic` (webui/traffic.py), tested without a browser
// (webui/tests/js/trafficanalysis.test.mjs); the screen draws what these return.
//
// Honesty: everything here comes from the mocks' own record (who asked, method and path shape, status class, latency). Latency is a
// histogram, so a percentile is a bucket's upper edge, never a made-up exact figure; a rate needs two samples; a period needs at
// least three repeats. Where the record cannot say, the functions return null and the screen says "not measured".

export const streamKey = s => `${s.who}|${s.service}|${s.shape}`;

// ---- rates -------------------------------------------------------------------------------------------------------------
// Requests a minute of one stream over the last `windowS` seconds, from samples [{t (s), n: {key: cumulative count}}].
// null until two samples at least 5 s apart exist, or when the count went down (a mock restarted).
export function rateFromSamples(samples, key, now, windowS = 60) {
  const w = samples.filter(s => s.t >= now - windowS && key in s.n);
  if (w.length < 2) return null;
  const a = w[0], b = w[w.length - 1], dt = b.t - a.t, dn = b.n[key] - a.n[key];
  return dt < 5 || dn < 0 ? null : dn / dt * 60;
}
export const sampleOf = (t, streams) => ({ t, n: Object.fromEntries(streams.map(s => [streamKey(s), s.n])) });

// ---- latency ----------------------------------------------------------------------------------------------------------
export function mergeHist(streams) {
  const len = Math.max(0, ...streams.map(s => s.hist.length)), out = Array(len).fill(0);
  for (const s of streams) s.hist.forEach((c, i) => { out[i] += c; });
  return out;
}
// The bucket holding the p-th fraction (0.95 = p95) of requests: {bucket, ms} where ms is the bucket's upper edge, Infinity above the last edge.
export function histPercentile(hist, edges, p) {
  const n = hist.reduce((a, b) => a + b, 0);
  if (!n) return null;
  let acc = 0;
  for (let i = 0; i < hist.length; i++) { acc += hist[i]; if (acc >= p * n) return { bucket: i, ms: i < edges.length ? edges[i] : Infinity }; }
  return null;
}
export const msWord = ms => ms === Infinity ? 'over the slowest bucket' : `${ms} ms or less`;

// ---- the river ---------------------------------------------------------------------------------------------------------
// Band width is proportional to the square root of the rate, so one busy stream does not hide the rest.
export const bandPx = (rpm, maxRpm, maxPx) => !(rpm > 0) || !(maxRpm > 0) ? 0 : maxPx * Math.sqrt(rpm / maxRpm);

// Who calls -> who answers -> how it ended. `rates` maps streamKey -> requests a minute (null = not measured yet).
// "Slower than p95" is the share of a stream's requests in latency buckets above the p95 bucket of all traffic; the histogram does not
// split by status, so slow answers are taken out of the ok count (never out of the errors).
export function riverModel(streams, edges, rates) {
  const p95 = histPercentile(mergeHist(streams), edges, 0.95), calls = new Map(), answers = new Map(), outcome = { ok: 0, slow: 0, client_error: 0, server_error: 0 };
  const rows = streams.map(s => {
    const rpm = rates[streamKey(s)] ?? null, slowN = p95 ? s.hist.reduce((a, c, i) => a + (i > p95.bucket ? c : 0), 0) : 0;
    const slow = Math.min(slowN, s.ok), share = { ok: (s.ok - slow) / s.n, slow: slow / s.n, client_error: s.client_error / s.n, server_error: s.server_error / s.n };
    return { key: streamKey(s), who: s.who, service: s.service, shape: s.shape, n: s.n, rpm, share };
  });
  for (const r of rows) {
    if (r.rpm === null) continue;
    const c = calls.get(r.who + '>' + r.service) || { who: r.who, service: r.service, rpm: 0 }; c.rpm += r.rpm; calls.set(r.who + '>' + r.service, c);
    answers.set(r.service, (answers.get(r.service) || 0) + r.rpm);
    for (const k of Object.keys(outcome)) outcome[k] += r.rpm * r.share[k];
  }
  return { p95, rows, calls: [...calls.values()], answers: Object.fromEntries(answers), outcome, measured: rows.some(r => r.rpm !== null) };
}

// ---- the pocket probe --------------------------------------------------------------------------------------------------
export const PROBE_MODES = ['Rate', 'Delay', 'Errors', 'Rhythm', 'Path'];
// One big reading and a one-line verdict for the chosen stream; null reading = not measured (never a zero).
export function probeReading(mode, stream, { rpm = null, edges = [], periods = [], callers = [] } = {}) {
  if (mode === 'Rate') return rpm === null ? { big: null, unit: '', verdict: 'Not enough samples yet: a rate needs two readings 5 seconds apart.' }
    : { big: Math.round(rpm * 10) / 10, unit: 'requests a minute', verdict: rpm < 1 ? 'Rare: less than one request a minute.' : rpm > 120 ? 'Busy: more than two requests a second.' : 'A steady trickle.' };
  if (mode === 'Delay') {
    const p50 = histPercentile(stream.hist, edges, 0.5), p95 = histPercentile(stream.hist, edges, 0.95);
    return !p50 ? { big: null, unit: '', verdict: 'No requests recorded.' }
      : { big: p50.ms, unit: p50.ms === Infinity ? '' : 'ms or less (median)', verdict: `Half of the answers took ${msWord(p50.ms)}; 95 % took ${msWord(p95.ms)}.` };
  }
  if (mode === 'Errors') {
    const bad = stream.client_error + stream.server_error, pct = Math.round(bad / stream.n * 1000) / 10;
    return { big: pct, unit: '% not 2xx', verdict: bad ? `${stream.server_error} server and ${stream.client_error} client errors in ${stream.n} requests.` : 'No errors recorded.' };
  }
  if (mode === 'Rhythm') return !periods.length ? { big: null, unit: '', verdict: 'No steady repeat found (it needs at least three repeats in the window).' }
    : { big: Math.round(periods[0].seconds * 10) / 10, unit: 'seconds between requests', verdict: `Repeats about every ${Math.round(periods[0].seconds * 10) / 10} s, run by ${stream.who}.` };
  if (mode === 'Path') return { big: stream.shape.split(' ').slice(1).join(' ') || stream.shape, unit: '', verdict: `${stream.shape.split(' ')[0]} on ${stream.service}, asked by ${callers.length ? callers.join(', ') : stream.who}.` };
  return null;
}

// ---- rhythm: repeated requests -----------------------------------------------------------------------------------------
// Periods in a list of request times (seconds): autocorrelation of the counts per small time bin. Returns up to three
// [{seconds, strength}], strongest first; [] when there are fewer than 8 requests or no lag with at least `threshold` correlation.
// Call it per stream (caller, service, path): a stream normally has one timer, and two timers mixed in one stream make beat patterns
// (their gaps) that look like periods of their own. A lag that is a whole multiple of a shorter one is the same timer, not another.
export function detectPeriods(times, { binS = 0.25, minS = 1, maxS = 120, threshold = 0.2 } = {}) {
  if (times.length < 8) return [];
  const t = [...times].sort((a, b) => a - b), span = t[t.length - 1] - t[0];
  const bin = Math.max(binS, span / 4000), L = Math.floor(span / bin) + 1, x = new Float64Array(L);
  for (const v of t) x[Math.floor((v - t[0]) / bin)] += 1;
  const mean = t.length / L; let r0 = 0;
  for (let i = 0; i < L; i++) { x[i] -= mean; r0 += x[i] * x[i]; }
  if (r0 === 0) return [];
  const lo = Math.max(2, Math.ceil(minS / bin)), hi = Math.min(Math.floor(maxS / bin), Math.floor(L / 3));
  const r = new Float64Array(hi + 2);
  for (let lag = lo - 1; lag <= hi + 1; lag++) { let s = 0; for (let i = 0; i + lag < L; i++) s += x[i] * x[i + lag]; r[lag] = s / r0 * (L / (L - lag)); }
  // Timing jitter splits one repeat over neighbouring lags, so a lag's strength is its own correlation plus its neighbours' positive correlation.
  const w = lag => Math.min(1, r[lag] + Math.max(0, r[lag - 1]) + Math.max(0, r[lag + 1])), peaks = [];
  const isTop = lag => { for (let d = -2; d <= 2; d++) { const q = lag + d; if (q >= lo - 1 && q <= hi + 1 && r[q] > r[lag]) return false; } return true; };
  for (let lag = lo; lag <= hi; lag++) if (w(lag) >= threshold && isTop(lag)) peaks.push({ lag, strength: w(lag) });
  const best = Math.max(0, ...peaks.map(p => p.strength));
  peaks.sort((a, b) => a.lag - b.lag);  // the shortest repeat first: a longer one that is a whole multiple of it is the same timer
  const out = [];
  for (const p of peaks) {
    if (p.strength < 0.25 * best) continue;
    if (out.some(q => { const k = Math.round(p.lag / q.lag); return k >= 2 && Math.abs(p.lag - k * q.lag) <= 0.06 * p.lag; })) continue;
    out.push(p);
  }
  out.sort((a, b) => b.strength - a.strength); out.length = Math.min(out.length, 3);
  return out.map(p => ({ seconds: Math.round(p.lag * bin * 100) / 100, strength: Math.round(p.strength * 100) / 100 }));
}

// ---- found in this window ----------------------------------------------------------------------------------------------
// `recent` rows are [t, who, service, shape, code, ms]; `periods` maps streamKey -> detectPeriods result.
export const RETRY_S = 15, BURST_BIN_S = 0.5, BURST_STREAMS = 4, BURST_BINS = 3;
export function findAnomalies(recent, periods = {}) {
  const found = [], by = new Map();
  for (const [t, who, service, shape, code] of recent) { const k = `${who}|${service}|${shape}`; (by.get(k) || by.set(k, []).get(k)).push({ t, code }); }
  for (const [k, rows] of by) {
    const [who, service, shape] = k.split('|'), errs = rows.filter(r => r.code >= 500);
    if (errs.length >= 3) {
      const retried = errs.filter(e => rows.some(r => r.t > e.t && r.t <= e.t + RETRY_S)).length;
      if (retried / errs.length < 0.2) found.push({ kind: 'no-retry', key: k, text: `${who} did not ask again after ${errs.length} server errors on ${shape}`, detail: `${retried} of ${errs.length} were followed by another request within ${RETRY_S} s (${service}).` });
    }
  }
  const bins = new Map();
  for (const [t, who, service, shape] of recent) { const b = Math.floor(t / BURST_BIN_S); (bins.get(b) || bins.set(b, new Set()).get(b)).add(`${who}|${service}|${shape}`); }
  const bursts = [...bins.values()].filter(s => s.size >= BURST_STREAMS);
  if (bursts.length >= BURST_BINS) found.push({ kind: 'burst', key: 'burst', text: `${Math.max(...bursts.map(s => s.size))} different calls fired in the same half second, ${bursts.length} times`, detail: 'Something starts several requests together (a refresh button, a poll that is not staggered).' });
  for (const [k, ps] of Object.entries(periods)) {
    const [who, service, shape] = k.split('|');
    if (ps.length && who === 'unknown') found.push({ kind: 'timer', key: k, text: `An unidentified caller repeats ${shape} every ${ps[0].seconds} s`, detail: `On ${service}; the request carries no User-Agent, so nothing says what runs this timer.` });
  }
  return found;
}

// ---- the focused wire and the window ---------------------------------------------------------------------------------------
// The URL hash is plain text, `#<cluster>-<node>` (`#workbench-gpu003`), or `#<node>` when there is one cluster to name.
export const wireHash = (cluster, node) => cluster ? `${cluster}-${node}` : node;
export function parseWireHash(hash, clusters, nodes) {
  const h = String(hash || '').replace(/^#/, '');
  if (!h) return null;
  for (const n of nodes) {
    if (h === n) return { cluster: clusters.length === 1 ? clusters[0] : null, node: n };
    for (const c of clusters) if (h === `${c}-${n}`) return { cluster: c, node: n };
  }
  return null;
}
export const windowRecent = (recent, now, windowS) => !Number.isFinite(windowS) ? recent : recent.filter(r => r[0] >= now - windowS);
// Requests per second of one stream over the last `spanS` seconds, oldest first: the mini trace of the probe.
export function perSecond(recent, key, now, spanS = 120) {
  const bins = Array(spanS).fill(0);
  for (const r of recent) { if (`${r[1]}|${r[2]}|${r[3]}` !== key) continue; const i = spanS - 1 - Math.floor(now - r[0]); if (i >= 0 && i < spanS) bins[i] += 1; }
  return bins;
}
// What can be said of a node's wire without a capture: only the documented wiring. Per-node counters do not exist (a node name in a path is
// masked as {id}); the messages come from an on-demand capture (the governor, class P5): tcpdump in a Slurm node, the API audit log for a Kubernetes node.
export function wireFacts(node) {
  if (!node) return null;
  const k8s = node.kind === 'k8s';
  return {
    source: k8s ? 'k8s' : 'slurm',
    links: k8s ? [{ to: 'KWOK', what: 'reports the node to the Kubernetes API', plane: 'k8s' }] : [{ to: 'BMC farm', what: 'Redfish: power, temperature, fans', plane: 'management' }, { to: 'slurmctld', what: 'Slurm control: state and jobs', plane: 'control' }],
    sourceText: k8s ? 'Kubernetes API audit events about this node: its lease and node object and its pods (who asked, what, how it ended; no bodies).'
      : 'Slurm messages seen by tcpdump inside this node on ports 6817 and 6818: direction, size, timing and the type from the message header (no bodies).',
  };
}

// ---- a capture: rows, the decoded message, the tube -----------------------------------------------------------------------------
export const clockOf = t => { const d = new Date(t * 1000); return d.toTimeString().slice(0, 8) + '.' + String(d.getMilliseconds()).padStart(3, '0'); };
const side = m => m.dir === 'in' ? 'to the node' : 'from the node';
export function captureRow(m) {
  return { time: m.t == null ? '' : clockOf(m.t), arrow: m.dir === 'in' ? '→' : '←', side: side(m), type: m.type, peer: m.peer_name || m.peer || 'unknown peer',
    size: m.size == null ? '' : `${m.size} B`, latency: m.latency_ms == null ? '' : `${m.latency_ms} ms`, result: m.result == null ? '' : String(m.result), unknown: m.kind === 'unknown' };
}
// The decoded view of one message: what the header or the audit event said, never a body. Missing facts are left out, not made up.
export function decodeFields(m) {
  if (!m) return [];
  const f = [['Message', m.type], ['Direction', side(m)], ['Peer', [m.peer_name, m.peer].filter(Boolean).join(' · ') || 'unknown'], ['Service port', m.port == null ? null : String(m.port)]];
  if (m.type_id != null) f.push(['Slurm type number', String(m.type_id)]);
  if (m.version != null) f.push(['Protocol version', `${m.version >> 8} (${m.version})`]);
  if (m.flags != null) f.push(['Flags', '0x' + m.flags.toString(16).padStart(4, '0')]);
  if (m.body_length != null) f.push(['Body length', `${m.body_length} B`]);
  if (m.size != null) f.push(['Message size', `${m.size} B`]);
  for (const [k, v] of [['Object', m.object], ['Asked by', m.who], ['Result', m.result == null ? null : String(m.result)], ['Answered in', m.latency_ms == null ? null : `${m.latency_ms} ms`], ['Note', m.note]]) if (v) f.push([k, v]);
  return f.filter(x => x[1] !== null && x[1] !== undefined);
}
export const shortType = t => String(t).replace(/^(REQUEST|RESPONSE|MESSAGE)_/, '').replace(/_/g, ' ').toLowerCase();
// The tube: the newest messages in each direction, requests to the node on one lane and what the node sends on the other.
export const tubeLanes = (msgs, n = 5) => ({ toNode: msgs.filter(m => m.dir === 'in').slice(-n), fromNode: msgs.filter(m => m.dir === 'out').slice(-n) });
