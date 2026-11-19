// The Test run workspace's logic, with no DOM (webui/tests/js/testrunmodel.test.mjs); testrun.js draws it.
// Honesty rule (as everywhere on the page): a number only where a counter backs it, otherwise "not measured", never 0.

// One short sentence per scenario step, for the Test run tab, the Run rail, the phone sheet and the badge labels. Every step kind the
// engine accepts (benchctl/scenario.py validate and _apply, k8s.STEP_KEYS, authfaults.KEYS, netemfaults.KEYS, the mocks' /sim/inject)
// has words; raw JSON is the last resort, and tests/test_step_words.py fails when a shipped scenario step falls back to it.
const list = a => (Array.isArray(a) ? a : a == null ? [] : [a]).join(', ');
const count = (k, one, many = one + 's') => `${k} ${+k === 1 ? one : many}`;
const nextCalls = k => +k === 1 ? 'the next call' : `the next ${k} calls`;
const pct = v => /%$/.test(String(v)) ? String(v) : `${v}%`;
// '60s' -> '60 s', '5m' -> '5 min', '200ms' -> '200 ms'; a bare number takes `unit`.
export function dur(v, unit = 's') {
  if (v == null || v === '') return '';
  const m = String(v).trim().match(/^(\d+(?:\.\d+)?)\s*(ms|s|m|min|h)?$/);
  if (!m) return String(v);
  const u = m[2] || unit;
  return `${m[1]} ${u === 'm' ? 'min' : u}`;
}
const user = d => d.user || 'alice';
const LOGIN = 'the login node';

function ssh(d) {
  const out = [];
  if (d.reset) out.push(`Restore ${LOGIN}'s SSH host key, keys and session limit`);
  if (d.state) out.push({ down: `Stop ${LOGIN} (SSH to it fails)`, up: `Bring ${LOGIN} back`, hung: `Freeze ${LOGIN} (SSH hangs)` }[d.state] || `Set ${LOGIN} to ${d.state}`);
  if (d.rotate_host_key) out.push(`Change ${LOGIN}'s SSH host key`);
  if (d.reject_auth) out.push(`Make ${LOGIN} refuse the client's SSH key`);
  if (d.node_sshd) out.push(d.node_sshd === 'down' ? `Stop sshd on ${list(d.nodes)} (the node stays up)` : `Start sshd again on ${list(d.nodes)}`);
  if (d.max_sessions != null) out.push(`Allow ${count(d.max_sessions, 'SSH session')} per connection on ${LOGIN}`);
  return out;
}

function auth(d) {
  const out = [], tk = d.slurm_token_ttl, tc = d.teleport_cert_ttl;
  if (tk) out.push(`Give ${user(tk)} a Slurm REST token that expires after ${dur(tk.ttl)}`);
  if (tc) out.push(`Make ${user(tc)}'s Teleport identity expire after ${dur(tc.ttl)}`);
  if (d.teleport_cert_restore) out.push(`Renew the Teleport identity of ${list(d.teleport_cert_restore)}`);
  if (d.munge_wrong_key) out.push(`Give ${list(d.munge_wrong_key)} a wrong munge key`);
  if (d.munge_restore) out.push(`Put the right munge key back on ${list(d.munge_restore)}`);
  if ('bmc_tls_expired' in d) out.push(d.bmc_tls_expired ? 'Expire the BMC farm\'s TLS certificate' : 'Give the BMC farm its valid TLS certificate again');
  if ('k8s_cert_expired' in d) out.push(d.k8s_cert_expired ? 'Expire the Kubernetes API server\'s certificate' : 'Give the Kubernetes API server its valid certificate again');
  if (d.kube_binding_missing) out.push(`Delete the Kubernetes binding of the ${list(d.kube_binding_missing)} tier (Teleport lets the user in, Kubernetes says forbidden)`);
  if ('kube_impersonation_revoked' in d) out.push(d.kube_impersonation_revoked ? 'Stop Teleport impersonating users in Kubernetes (every tsh kube request fails)' : 'Let Teleport impersonate users in Kubernetes again');
  if (d.kube_binding_restore) out.push('Put back the Kubernetes bindings of the Teleport access tiers');
  return out;
}

const POD_FAULT = { crashloop: 'that crash-loops', oom: 'that runs out of memory', imagepull: 'whose image cannot be pulled', jobfail: 'whose job fails' };
function k8s(d) {
  const out = [], kn = k => `Kubernetes node${d[k].length === 1 ? '' : 's'} ${list(d[k])}`;
  if (d.drain && !Array.isArray(d.drain)) out.push(`Drain Kubernetes node ${d.drain.node} (waits ${d.drain.timeout || 30} s for evictions)`);
  if (Array.isArray(d.down)) out.push(`Take ${kn('down')} down (NotReady)`);
  if (Array.isArray(d.up)) out.push(`Bring ${kn('up')} back (Ready)`);
  if (Array.isArray(d.cordon)) out.push(`Cordon ${kn('cordon')}`);
  if (Array.isArray(d.uncordon)) out.push(`Uncordon ${kn('uncordon')}`);
  if (d.pod) {
    const p = d.pod, extra = [p.gpus ? count(p.gpus, 'GPU') : '', p.priority_class ? `priority ${p.priority_class}` : ''].filter(Boolean);
    out.push(`Start pod ${p.namespace}/${p.name}${p.node ? ` on ${p.node}` : ''}${p.fault ? ` ${POD_FAULT[p.fault] || `with fault ${p.fault}`}` : ''}${extra.length ? ` (${extra.join(', ')})` : ''}`);
  }
  if (d.delete_pod) out.push(`Delete pod ${d.delete_pod.namespace}/${d.delete_pod.name}`);
  if (d.scale) out.push(`Scale deployment ${d.scale.namespace}/${d.scale.deploy} to ${count(d.scale.replicas, 'replica')}`);
  if (d.apply) out.push(`Apply k8s/${list(d.apply)} to the Kubernetes API`);
  if (d.remove) out.push(`Remove k8s/${list(d.remove)} from the Kubernetes API`);
  if (d.queue) {
    const q = d.queue, pend = q.pending != null ? ` (${count(q.pending, 'workload')} pending)` : '';
    out.push(q.active === false ? `Stop queue ${q.name} admitting work${pend}` : q.active === true ? `Let queue ${q.name} admit work again${pend}` : `Set queue ${q.name} to ${count(q.pending, 'pending workload')}`);
  }
  if (d.metrics_server === 'stop') out.push('Stop metrics-server (the metrics API answers 503)');
  else if (d.metrics_server === 'start') out.push('Start metrics-server again (the metrics API answers)');
  if (d.gpu) {
    const g = d.gpu;
    out.push(g.advertised !== undefined ? `Kubernetes node ${g.node} ${g.advertised ? 'advertises its GPUs again' : 'stops advertising its GPUs (allocatable 0)'}` : `Kubernetes node ${g.node} ${g.mig.strategy === 'none' ? 'is a normal GPU node again' : `becomes a MIG node (${g.mig.strategy}, ${g.mig.profile || '1g.18gb'})`}`);
  }
  if (d.kubelet) out.push(d.kubelet.skew === 'same' ? `Kubernetes node ${d.kubelet.node} reports the API server's version` : `Kubernetes node ${d.kubelet.node} reports a kubelet ${d.kubelet.skew} than the API server`);
  if (d.volcano) out.push(d.volcano.queue ? `Set Volcano queue ${d.volcano.queue} ${String(d.volcano.state).toLowerCase()}` : `Set Volcano PodGroup ${d.volcano.namespace}/${d.volcano.podgroup} to ${d.volcano.phase}`);
  if (d.usage) out.push(`Set the CPU use of each ${d.usage.namespace}/${d.usage.deploy} pod to ${d.usage.cpu} (its autoscaler reacts)`);
  return out;
}

function netem(d) {
  if (d.clear) return ['Remove the network delay and loss from every node'];
  const what = [d.delay != null ? `${dur(d.delay, 'ms')} delay` : '', d.loss != null ? `${pct(d.loss)} packet loss` : ''].filter(Boolean);
  return [`Add ${what.join(' and ') || 'a network fault'} on ${list(d.nodes)}${d.ttl ? ` for ${dur(d.ttl)}` : ''}`];
}

const REAL = {
  outage: () => 'Freeze the real Jira (its requests time out)',
  restore: () => 'Unfreeze the real Jira',
  bad_logins: d => `Send ${count(d.count ?? 5, 'wrong-password login')} for ${user(d)} to the real Jira (it locks with a CAPTCHA)`,
  clear_logins: () => 'Reset the failed-login count on the real Jira',
  rate_limit: d => `Turn on the real Jira's rate limit (${count(d.capacity ?? 3, 'request')}, ${d.fill_rate ?? 1} more per second)`,
  rate_limit_off: () => 'Turn off the real Jira\'s rate limit',
  token_revoke: d => `Revoke ${user(d)}'s API token on the real Jira`,
  token_restore: d => `Give ${user(d)} a new API token on the real Jira`,
  proxy_503: d => `Make the real Jira's proxy answer 503 for ${nextCalls(d.count ?? 1)} (Retry-After ${d.retry_after ?? 30} s)`,
  proxy_429: d => `Make the real Jira's proxy answer 429 for ${nextCalls(d.count ?? 1)} (Retry-After ${d.retry_after ?? 30} s)`,
  proxy_slow: d => `Make the real Jira's proxy answer ${dur(d.delay ?? 2)} late for ${nextCalls(d.count ?? 1)}`,
  proxy_off: () => 'Take the faults off the real Jira\'s proxy',
};
function jiraReal(d) {
  if (d.tickets) return [tickets(d.tickets, 'real Jira')];
  return REAL[d.fault] ? [REAL[d.fault](d)] : [];
}

function tickets(t, jira = 'Jira') {
  if (t.action === 'fault') return t.open === false ? `Resolve ${t.node}'s ${t.kind || 'fault'} ${jira} ticket` : `File a ${t.kind || 'fault'} ${jira} ticket for ${t.node}`;
  if (t.action === 'reset') return `Delete every ${jira} ticket`;
  if (t.action === 'transition') return `Move the matching ${jira} tickets to ${t.to}`;
  if (t.action === 'comment') return `Comment on the matching ${jira} tickets`;
  return `${{ create: 'Create' }[t.action] || t.action} ${t.count ? t.count + ' ' : ''}${jira} ticket${t.count === undefined || t.count === 1 ? '' : 's'}${t.summary ? `: ${t.summary}` : ''}`;
}

function storage(st, n) {
  const m = st.mount || 'the mount', out = [];
  if (st.reset) return [`Put ${m} back to normal (seen from ${n})`];
  if (st.used_pct != null) out.push(`Fill ${m} to ${pct(st.used_pct)} used${st.inodes_used_pct != null ? `, ${pct(st.inodes_used_pct)} of its inodes` : ''} (seen from ${n})`);
  else if (st.inodes_used_pct != null) out.push(`Use ${pct(st.inodes_used_pct)} of ${m}'s inodes (seen from ${n})`);
  if (st.stale != null) out.push(st.stale ? `Make ${m} stale on ${n}` : `Make ${m} answer again on ${n}`);
  if (st.user) out.push(`Put ${st.user} at ${st.used_gb} GB on ${m} (seen from ${n})`);
  return out;
}

const DAEMON = { slurmd_restart: 'Restart', slurmd_stop: 'Stop', slurmd_start: 'Start' };
// The keyed steps of the mocks and Slurm: one sentence, from the first key that says what the step does.
function keyed(d, n, t) {
  if (d.drain && !Array.isArray(d.drain)) return k8s(d);
  if (d.drain) return [`Drain ${d.drain.join(', ')} in Slurm (${d.reason || 'scenario'})`];
  if (Array.isArray(d.down)) return [`Mark ${d.down.join(', ')} down in Slurm (${d.reason || 'scenario'})`];
  if (d.resume) return [`Resume ${d.resume.join(', ')} in Slurm`];
  if (d.power_off) return [`Freeze ${list(d.power_off)} (it stops answering, as after a power cut)`];
  if (d.power_on) return [`Unfreeze ${list(d.power_on)}`];
  if (d.reboot) return [`Reboot ${list(d.reboot)} through Slurm (scontrol reboot ASAP)`];
  if (d.power_down) return [`Power ${list(d.power_down)} down through Slurm's power saving`];
  if (d.power_up) return [`Power ${list(d.power_up)} up through Slurm's power saving`];
  if (d.submit) return [`Submit a ${d.submit.duration || 600} s job on ${d.submit.node}${d.submit.gpus ? ` holding ${d.submit.gpus} GPUs` : ''}${d.submit.user ? ` as ${d.submit.user}` : ''}`];
  if (d.cancel) return ['Cancel the simulated users\' jobs'];
  for (const k of Object.keys(DAEMON)) if (d[k]) return [`${DAEMON[k]} slurmd on ${d[k].join(', ')}`];
  if (d.slurmctld_restart) return ['Restart slurmctld'];
  if (d.tickets) return [tickets(d.tickets)];
  if (d.activity) return [d.activity.on === false ? 'Stop the background Jira activity' : `Start background Jira activity (a change every ${d.activity.every || 10} s)`];
  if (d.failure) {
    const f = d.failure, next = `for ${nextCalls(f.count || 1)}`;
    return [!f.mode ? 'Jira back to normal' : f.mode === 'slow' ? `Make Jira answer ${dur(f.delay ?? 2)} late ${next}` : `Make Jira answer ${f.mode} ${next}`];
  }
  if (d.storage) return storage(d.storage, n);
  if (d.clear) return [`Clear injected faults on ${n}`];
  if (d.power) return [`Power ${d.power} ${n}`];
  if (d.closed) return [`Close ${n} in BCM`];
  if (d.state) return [`Set ${n} to ${d.state} in BCM`];
  if (d.xid) return [`GPU error XID ${d.xid} on ${n}`];
  if (d.event) return [`BMC event on ${n} (${d.severity || 'Warning'}): ${d.event}`];
  if (d.temp != null) return [`Temperature of ${n} to ${d.temp} °C`];
  if (d.fan_fail) return [`${d.fan_fail} fan(s) fail on ${n}`];
  if (d.ib_down != null) return [+d.ib_down ? `Take ${count(d.ib_down, 'InfiniBand port')} down on ${n}` : `Bring the InfiniBand ports back up on ${n}`];
  if (d.nic_flap != null) return [d.nic_flap ? `Make the Ethernet link flap on ${n}` : `Stop the Ethernet link flapping on ${n}`];
  if (typeof d.down === 'boolean') return [d.down ? `Make the GPU exporter stop answering for ${n}` : `Make the GPU exporter answer again for ${n}`];
  return [];
}

const BY_TARGET = {
  ssh, auth, k8s, netem, 'jira-real': jiraReal,
  switch: d => [d.state === 'up' ? `Bring leaf switch ${d.leaf} back up` : `Take leaf switch ${d.leaf} down (its nodes drop out)`],
  'slurm-upgrade': d => [`Upgrade ${list((d.daemons || []).map(x => x === 'login' ? LOGIN : x))} to Slurm ${d.to}`],
  dcgm: d => { const v = ['stop', 'start', 'restart'].find(k => d[k]); return v ? [`${v[0].toUpperCase()}${v.slice(1)} the real DCGM exporter on ${list(d.nodes)}`] : []; },
  fault: d => d.inject ? [`Inject the ${d.inject} fault on ${list(d.nodes)}`] : d.clear ? [`Clear the ${d.clear} fault on ${list(d.nodes)}`] : [],
};

export function describe(s) {
  const d = s.do || {}, by = BY_TARGET[s.target], words = by ? by(d) : keyed(d, list(d.nodes), s.target);
  return words.length ? words.join('; ') : JSON.stringify(d);
}
// One sentence per expectation. The engine's label ('slurm.node_state {"node": "cn001", ...}', benchctl/scenario.py expectation()) stays
// the key that joins plan and result; the page shows these words. tests/test_step_words.py fails when a shipped expectation shows JSON.
const SLURM_STATE = { drain: 'drained', down: 'down', idle: 'idle', alloc: 'allocated', mix: 'mixed', resv: 'reserved' };
const jiraOf = a => a.target === 'real' ? 'real Jira' : 'Jira';
const pctUsed = (v, what) => `at least ${pct(v)} ${what}`;
const EXPECT = {
  'slurm.node_state': a => `Slurm shows ${a.node} as ${SLURM_STATE[String(a.state).toLowerCase()] || a.state}`,
  'slurm.job_state': a => `A job on ${a.node} is ${String(a.state).toLowerCase()} in Slurm`,
  'slurm.stepd': a => a.alive === false ? `No slurmstepd runs on ${a.node}` : `A slurmstepd runs on ${a.node}`,
  'k8s.node_ready': a => `Kubernetes node ${a.node} is ${a.ready === false ? 'NotReady' : 'Ready'}`,
  'k8s.pod_status': a => `Pod ${a.namespace}/${a.name} shows ${a.reason}`,
  'k8s.metrics_api': a => `The metrics API ${a.served === false ? 'is unavailable' : 'answers'}`,
  'k8s.node_gpus': a => `Kubernetes node ${a.node} allocates ${a.allocatable ?? 0} of ${a.resource || 'nvidia.com/gpu'}`,
  'k8s.node_kubelet': a => `Kubernetes node ${a.node} reports a kubelet ${a.skew || 'different'} than the API server`,
  'k8s.pods_on_node': a => `At least ${count(a.min ?? 1, 'Running pod')} (${a.selector}, ${a.namespace}) on Kubernetes node ${a.node}`,
  'storage.mount': a => `${a.mount} on ${a.node || 'cn001'}: ${[a.min_used_pct != null ? pctUsed(a.min_used_pct, 'used') : '', a.min_inodes_used_pct != null ? pctUsed(a.min_inodes_used_pct, 'of its inodes used') : '',
    a.quota_exceeded ? `${a.quota_exceeded} over quota` : '', a.stale != null ? (a.stale ? 'stale' : 'not stale') : ''].filter(Boolean).join(', ') || 'mounted'}`,
  'jira.issue_created': a => `A ${jiraOf(a)} ticket is filed with the labels ${list(a.labels)}`,
  'jira.issue_status': a => `${a.count != null ? count(a.count, `${jiraOf(a)} ticket`) : `The ${jiraOf(a)} tickets`} matching “${a.jql}” ${+a.count === 1 ? 'is' : 'are'} ${a.status}`,
  'jira.issue_count': a => `${count(a.count, `${jiraOf(a)} ticket`)} ${+a.count === 1 ? 'matches' : 'match'} “${a.jql}”`,
};
export function expectWords(label) {
  const m = String(label || '').match(/^(\S+) (\{.*\})$/s);
  if (!m || !EXPECT[m[1]]) return String(label || '');
  try { return EXPECT[m[1]](JSON.parse(m[2])); } catch (e) { return String(label); }
}

export function plain(x) {
  if (!x.detail) return '';
  if (/unreachable/.test(x.detail)) return `can't reach ${x.target}: is the stack running?`;
  if (/Slurm steps need/.test(x.detail)) return 'needs the host CLI';
  return x.detail;
}

// Timeline rows: the preview (what the run will do, and which steps the page cannot do) joined by index with the run state (what happened).
// A row the page cannot run says so before Play; after the run its status is "skipped", never a pass.
export function timelineRows(preview, run) {
  const steps = (preview && preview.steps) || (run && run.steps) || [], live = run && preview && run.name === preview.name ? run.steps : null;
  return steps.map((p, i) => {
    const r = live && live[i], page = p.page !== false;
    return { at: p.at, text: describe(p), target: p.target, page, why: page ? '' : (p.why || 'needs the host CLI'),
      status: r ? r.status : 'pending', note: r && r.detail ? plain(r) : '', t: r && r.t != null ? r.t : null };
  });
}

export function runVerdict(run) {
  if (!run || !run.name) return null;
  if (run.running) return 'running';
  if (run.stopped) return 'stopped';
  const skipped = (run.steps || []).some(x => x.status === 'skipped') || (run.expects || []).some(x => x.status === 'skipped');
  return !run.ok ? 'failed' : skipped ? 'passed with skips' : 'passed';
}

// Client's eye: per service the counters the mock keeps. snap is the flow snapshot ({reachable, totals, errors}); base is the same at Play.
export function eyeRows(snap, base, nameOf = k => k) {
  if (!snap || !snap.reachable) return [];
  return Object.keys(snap.reachable).sort().map(k => {
    const measured = !!snap.totals && k in snap.totals, tot = measured ? snap.totals[k] : null, up = !!snap.reachable[k];
    const b = base && base.totals && k in base.totals ? base : null;
    const during = measured && b ? Math.max(0, tot - b.totals[k]) : null;
    const errDuring = measured && b && snap.errors && k in snap.errors && b.errors && k in b.errors ? Math.max(0, snap.errors[k] - b.errors[k]) : null;
    const verdict = !up ? 'not reachable' : !measured ? 'not measured' : (b ? during : tot) > 0 ? 'called' : 'not called yet';
    return { key: k, name: nameOf(k), up, total: tot, during, errors: errDuring, verdict };
  });
}
export const eyeBase = snap => snap && snap.totals ? { totals: { ...snap.totals }, errors: { ...(snap.errors || {}) } } : null;

const cell = v => v == null ? 'n/a' : String(v);
const iso = secs => new Date(secs * 1000).toISOString().replace(/\.\d+Z$/, 'Z');
const ranAt = t => t == null ? 'did not run' : `${t} s`;
// The exportable report (what was injected, what the client called, what passed): plain data and Markdown, no hostnames, tokens or paths.
// Times (ADR 0086 phase 3): `started` is the page process's clock at Play (the run state's `started`, else the browser's); each step's and
// each expectation's `t` is seconds after Play (null: it did not run), `duration_s` the run's `elapsed`.
export function buildReport({ preview, run, startedAt, finishedAt, speed, eye }) {
  const rows = timelineRows(preview, run), expects = (run && run.expects) || [], verdict = runVerdict(run) || 'not run';
  const t0 = run && typeof run.started === 'number' ? run.started : null, ended = run && !run.running && run.elapsed != null;
  const data = {
    scenario: preview ? preview.name : run && run.name, ref: preview ? preview.ref : '', speed: speed || (run && run.speed) || 1, verdict,
    ran_from: run && run.source === 'cli' ? 'cli' : 'page',  // ADR 0086 phase 4: `benchctl scenario run` posted it to the page
    started: t0 != null ? iso(t0) : startedAt || null, finished: t0 != null && ended ? iso(t0 + run.elapsed) : finishedAt || null,
    duration_s: run && run.elapsed != null ? run.elapsed : null,
    steps: rows.map(r => ({ at: r.at, t: r.t, target: r.target, action: r.text, status: r.status, page_could_run: r.page, note: r.note || r.why })),
    expectations: expects.map(x => ({ label: x.label, words: expectWords(x.label), status: x.status, t: x.t != null ? x.t : null, detail: x.detail || '' })),
    clients_eye: eye.map(e => ({ service: e.key, calls_during_run: e.during, errors_during_run: e.errors, verdict: e.verdict })),
  };
  const md = [`# Test run: ${data.scenario}${data.ref ? ` (${data.ref})` : ''}`, '',
    `- Result: **${verdict}**`, `- Ran from: ${data.ran_from === 'cli' ? 'the CLI (benchctl scenario run)' : 'the web page'}`, `- Speed: ${data.speed}×`, `- Started: ${data.started || 'not captured (the run began before this page was opened)'}`, `- Finished: ${data.finished || 'n/a'}`,
    `- Took: ${data.duration_s == null ? 'n/a' : `${data.duration_s} s`}`, '',
    '## What was injected', '', 'At: planned, seconds into the scenario. Ran at: seconds after Play, at this speed.', '',
    '| At | Ran at | Target | Action | Result |', '|---|---|---|---|---|',
    ...data.steps.map(s => `| ${s.at.toFixed(0)} s | ${ranAt(s.t)} | ${s.target} | ${s.action.replace(/\|/g, '/')} | ${s.status}${s.note ? `: ${s.note.replace(/\|/g, '/')}` : ''} |`), '',
    '## Expectations', '', ...(data.expectations.length ? data.expectations.map(x => `- ${x.status}${x.t != null ? ` at ${x.t} s` : ''}: ${x.words}${x.detail ? ` (${x.detail})` : ''}`) : ['- none declared']), '',
    '## What the client called (mock counters during the run)', '', '| Service | Calls | Errors | Verdict |', '|---|---|---|---|',
    ...data.clients_eye.map(e => `| ${e.service} | ${cell(e.calls_during_run)} | ${cell(e.errors_during_run)} | ${e.verdict} |`), '',
    data.ran_from === 'cli' ? 'Skipped steps were skipped by the CLI; they are not passes. n/a means the mock keeps no counter for it.'
      : 'Skipped steps were not run by the page (they need the host CLI); they are not passes. n/a means the mock keeps no counter for it.', ''];
  return { data, markdown: md.join('\n') };
}


// The scenario library's group chips (issue 861 D2). A scenario belongs to the first group, in this order, that one of its step targets
// maps to; so a Kubernetes scenario that also touches Slurm is under Kubernetes. A target with no group lands under "Other".
export const SCN_GROUPS = [['auth', 'Auth'], ['k8s', 'Kubernetes'], ['jira', 'Jira'], ['ssh', 'SSH'], ['network', 'Network'], ['hardware', 'Hardware'], ['slurm', 'Slurm'], ['other', 'Other']];
const TARGET_GROUP = { auth: 'auth', k8s: 'k8s', jira: 'jira', 'jira-real': 'jira', ssh: 'ssh', netem: 'network', switch: 'network',
  'bmc-farm': 'hardware', 'gpu-exporter': 'hardware', dcgm: 'hardware', fault: 'hardware', 'bcm-mock': 'hardware', slurm: 'slurm', 'slurm-upgrade': 'slurm' };
export function scenarioGroup(targets) {
  const have = new Set((targets || []).map(t => TARGET_GROUP[t] || 'other'));
  return (SCN_GROUPS.find(([k]) => have.has(k)) || ['other'])[0];
}
export function groupCounts(list) {  // [[key, label, count], ...] for the groups that have a scenario, after "All"
  const n = {}; for (const s of list) { const g = scenarioGroup(s.targets); n[g] = (n[g] || 0) + 1; }
  return SCN_GROUPS.filter(([k]) => n[k]).map(([k, l]) => [k, l, n[k]]);
}
export function firstSentence(text) {  // the card's line: what the client should notice; the rest is in the detail pane (D3)
  const t = String(text || '').trim(), re = /[.!?](?=\s|$)/g;
  for (let m; (m = re.exec(t));) { if (!/(?:e\.g|i\.e|etc|vs)$/i.test(t.slice(0, m.index))) return t.slice(0, m.index + 1); }
  return t;
}
