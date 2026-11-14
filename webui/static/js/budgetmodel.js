// Plan sheet, projected memory (W-7) as data. Pure, so `node --test` covers it. Read-only: it only formats what /api/plan returned.
// The governor's admission (GV-9) stays the one that blocks; this is the same arithmetic, shown before Apply.
const mb = n => (n < 0 ? '−' : '') + Math.abs(Math.round(n)).toLocaleString('en-US') + ' MB';
const gb = n => (n / 1024).toFixed(1) + ' GB';

// p: the /api/plan answer -> {summary, over: null | {icon, title, text}, rows: [{name, expected, cap}]}
export function budgetView(p) {
  const short = -p.headroom_mb, over = p.headroom_mb < 0;
  const summary = `Projected need ${gb(p.need_mb)} of a ${gb(Math.max(p.budget_mb, 0))} budget (${gb(p.available_mb)} free minus a ${gb(p.floor_mb)} safety floor). ` +
    (over ? `Over by ${mb(short)}.` : `${mb(p.headroom_mb)} to spare.`);
  const warn = over ? { icon: '▲', title: 'Over budget', text: `This selection needs ${mb(p.need_mb)} and the budget is ${mb(Math.max(p.budget_mb, 0))}: short by ${mb(short)}. The governor will refuse it at Apply.` } : null;
  const rows = (p.breakdown || []).map(r => ({ name: r.name, expected: mb(r.expected_mb), cap: r.cap_mb == null ? '–' : mb(r.cap_mb) }));
  return { summary, over: warn, rows };
}
