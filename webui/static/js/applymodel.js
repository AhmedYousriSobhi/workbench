// Apply progress (U5) as data. The governor reports a job id and the stages of `benchctl up` in its status (`status.change`);
// the page shows them and never reads log wording. Pure, so `node --test` covers it.

const ICON = { waiting: '○', running: '▶', done: '✓', failed: '✗' };
const WORD = { waiting: 'waiting', running: 'running', failed: 'failed' };

// status.change -> [{name, state, icon, t}] for the Progress list; `t` is words, never an invented time
export function stageRows(change) {
  if (!change || !Array.isArray(change.stages)) return [];
  return change.stages.map(s => ({
    name: s.name, state: s.state, icon: ICON[s.state] || '?',
    t: s.state === 'done' ? (typeof s.secs === 'number' ? `${Math.round(s.secs)} s` : 'done') : WORD[s.state] || s.state,
  }));
}

// what the page should do about job `job`: still running, finished, or not reported (governor restarted, or an older governor)
export function applyOutcome(change, job) {
  if (!change || change.job !== job) return { kind: 'unknown' };
  if (change.state === 'running') {
    const rows = stageRows(change), done = rows.filter(r => r.state === 'done').length, now = rows.find(r => r.state === 'running');
    return { kind: 'running', text: rows.length ? `Job #${job}: ${now ? now.name.toLowerCase() : 'working'} (${done} of ${rows.length} stages done)` : `Job #${job}: starting` };
  }
  const secs = change.finished && change.started ? Math.round(change.finished - change.started) : null, took = secs === null ? '' : `, ${secs} s`;
  if (change.state === 'done') return { kind: 'done', text: `Applied (job #${job}${took}).` };
  const at = (change.stages || []).find(s => s.state === 'failed');
  return { kind: 'failed', text: `Failed${at ? ` at ${at.name.toLowerCase()}` : ''} (job #${job}${took}). The reason is in Governor activity.` };
}
