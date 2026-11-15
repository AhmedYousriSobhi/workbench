// The Test run workspace's logic, with no DOM (webui/tests/js/testrunmodel.test.mjs); testrun.js draws it.
// Honesty rule (as everywhere on the page): a number only where a counter backs it, otherwise "not measured", never 0.

export function describe(s) {
  const d = s.do, n = (d.nodes || []).join(', ');
  if (d.drain && !Array.isArray(d.drain)) return `Drain Kubernetes node ${d.drain.node} (waits ${d.drain.timeout || 60} s for evictions)`;
  if (d.drain) return `Drain ${d.drain.join(', ')} in Slurm (${d.reason || 'scenario'})`;
  if (d.resume) return `Resume ${d.resume.join(', ')} in Slurm`;
  if (d.submit) return `Submit a ${d.submit.duration || 600} s job on ${d.submit.node}${d.submit.gpus ? ` holding ${d.submit.gpus} GPUs` : ''}`;
  if (d.cancel) return 'Cancel the simulated users\' jobs';
  for (const k of ['slurmd_restart', 'slurmd_stop', 'slurmd_start']) if (d[k]) return `${{ slurmd_restart: 'Restart', slurmd_stop: 'Stop', slurmd_start: 'Start' }[k]} slurmd on ${d[k].join(', ')}`;
  if (d.slurmctld_restart) return 'Restart slurmctld';
  if (d.tickets) { const t = d.tickets; return `${{ create: 'Create', transition: 'Move', comment: 'Comment on' }[t.action] || t.action} ${t.count ? t.count + ' ' : ''}Jira ticket${t.count === undefined || t.count === 1 ? '' : 's'}${t.summary ? `: ${t.summary}` : t.to ? ` to ${t.to}` : ''}`; }
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

// Timeline rows: the preview (what the run will do, and which steps the page cannot do) joined by index with the run state (what happened).
// A row the page cannot run says so before Play; after the run its status is "skipped", never a pass.
export function timelineRows(preview, run) {
  const steps = (preview && preview.steps) || (run && run.steps) || [], live = run && preview && run.name === preview.name ? run.steps : null;
  return steps.map((p, i) => {
    const r = live && live[i], page = p.page !== false;
    return { at: p.at, text: describe(p), target: p.target, page, why: page ? '' : (p.why || 'needs the host CLI'),
      status: r ? r.status : 'pending', note: r && r.detail ? plain(r) : '' };
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
// The exportable report (what was injected, what the client called, what passed): plain data and Markdown, no hostnames, tokens or paths.
export function buildReport({ preview, run, startedAt, finishedAt, speed, eye }) {
  const rows = timelineRows(preview, run), expects = (run && run.expects) || [], verdict = runVerdict(run) || 'not run';
  const data = {
    scenario: preview ? preview.name : run && run.name, ref: preview ? preview.ref : '', speed: speed || (run && run.speed) || 1, verdict,
    started: startedAt || null, finished: finishedAt || null,
    steps: rows.map(r => ({ at: r.at, target: r.target, action: r.text, status: r.status, page_could_run: r.page, note: r.note || r.why })),
    expectations: expects.map(x => ({ label: x.label, status: x.status, detail: x.detail || '' })),
    clients_eye: eye.map(e => ({ service: e.key, calls_during_run: e.during, errors_during_run: e.errors, verdict: e.verdict })),
  };
  const md = [`# Test run: ${data.scenario}${data.ref ? ` (${data.ref})` : ''}`, '',
    `- Result: **${verdict}**`, `- Speed: ${data.speed}×`, `- Started: ${data.started || 'not captured (the run began before this page was opened)'}`, `- Finished: ${data.finished || 'n/a'}`, '',
    '## What was injected', '', '| At | Target | Action | Result |', '|---|---|---|---|',
    ...data.steps.map(s => `| ${s.at.toFixed(0)} s | ${s.target} | ${s.action.replace(/\|/g, '/')} | ${s.status}${s.note ? `: ${s.note.replace(/\|/g, '/')}` : ''} |`), '',
    '## Expectations', '', ...(data.expectations.length ? data.expectations.map(x => `- ${x.status}: ${x.label}${x.detail ? ` (${x.detail})` : ''}`) : ['- none declared']), '',
    '## What the client called (mock counters during the run)', '', '| Service | Calls | Errors | Verdict |', '|---|---|---|---|',
    ...data.clients_eye.map(e => `| ${e.service} | ${cell(e.calls_during_run)} | ${cell(e.errors_during_run)} | ${e.verdict} |`), '',
    'Skipped steps were not run by the page (they need the host CLI); they are not passes. n/a means the mock keeps no counter for it.', ''];
  return { data, markdown: md.join('\n') };
}
