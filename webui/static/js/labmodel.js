// The Lab workspace's logic, no DOM (issue 556, W-17l): the stepper, the verdict wording, the paired result bars, the diff notes.
export const STEP_NAMES = [['import', 'Import'], ['validate', 'Validate'], ['diff', 'Diff'], ['apply', 'Apply'], ['replay', 'A/B replay'], ['report', 'Report']];
export const VERDICT_STATE = { 'Do not ship': 'bad', 'Ship with changes': 'warn', 'Ship': 'ok', 'No effect': 'ok' };  // the word always shows beside the state (never colour alone)

export function stepRows(steps) {
  return STEP_NAMES.map(([k, label]) => ({ key: k, label, state: (steps && steps[k]) || 'todo' }));
}
export function verdictView(verdict) {
  return verdict ? { text: verdict, state: VERDICT_STATE[verdict] || 'warn' } : { text: 'No verdict (candidate-only or not comparable)', state: 'warn' };
}
// one row per partition: the two waits in production-equivalent seconds, and each bar's width against the larger of the two
export function resultBars(report) {
  if (!report || !report.b) return [];
  const f = Number(report.factor) || 1, names = new Set([...Object.keys((report.a && report.a.by_partition) || {}), ...Object.keys(report.b.by_partition || {})]);
  return [...names].sort().map(p => {
    const a = report.a && report.a.by_partition[p], b = report.b.by_partition[p];
    const wa = a && a.wait_median_s != null ? a.wait_median_s * f : null, wb = b && b.wait_median_s != null ? b.wait_median_s * f : null;
    const top = Math.max(wa || 0, wb || 0, 1);
    return { partition: p || '(none)', a: wa, b: wb, aPct: wa == null ? 0 : Math.round(100 * wa / top), bPct: wb == null ? 0 : Math.round(100 * wb / top) };
  });
}
export function secs(v) {
  if (v == null) return '–';
  if (v < 90) return `${Math.round(v)} s`;
  return v < 5400 ? `${Math.round(v / 60)} min` : `${(v / 3600).toFixed(1)} h`;
}
// a diff row's tag: how a change reaches the cluster (live, restart), plus the notes the diff already carries
export function diffTag(row) {
  const n = (row.note || '').toLowerCase();
  if (n.includes('restart')) return 'restart';
  if (n.includes('not tested')) return 'not tested';
  if (n.includes('time')) return 'time-scaled';
  return 'live';
}
export function validationRows(v) {
  return ((v && v.results) || []).map(r => ({ tool: r.tool, status: r.status, first: (r.messages || [])[0] || '' }));
}
// the one message for a refused or failed call
export function refusal(code, detail) {
  return code === 503 ? 'The governor is not reachable: start it with ./scripts/benchctl start' : String(detail || 'refused');
}
