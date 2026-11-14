import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { budgetView } from '../../static/js/budgetmodel.js';

const plan = o => ({ need_mb: 4096, available_mb: 8192, floor_mb: 1024, budget_mb: 7168, headroom_mb: 3072,
  breakdown: [{ name: '2 nodes', expected_mb: 1000, cap_mb: 2048 }, { name: 'already running', expected_mb: -200, cap_mb: null }], ...o });

test('under budget: the spare memory is said and there is no warning', () => {
  const v = budgetView(plan());
  assert.match(v.summary, /4\.0 GB of a 7\.0 GB budget/); assert.match(v.summary, /3,072 MB to spare/); assert.equal(v.over, null);
});
test('over budget: a warning with an icon, the shortfall and the governor named', () => {
  const v = budgetView(plan({ need_mb: 9000, headroom_mb: -1832 }));
  assert.equal(v.over.icon, '▲'); assert.match(v.over.title, /Over budget/); assert.match(v.over.text, /short by 1,832 MB/); assert.match(v.over.text, /governor/); assert.match(v.summary, /Over by 1,832 MB/);
});
test('rows keep negatives and show a dash where there is no cap; a negative budget reads as zero', () => {
  const v = budgetView(plan({ budget_mb: -500, headroom_mb: -4596 }));
  assert.deepEqual(v.rows, [{ name: '2 nodes', expected: '1,000 MB', cap: '2,048 MB' }, { name: 'already running', expected: '−200 MB', cap: '–' }]);
  assert.match(v.summary, /0\.0 GB budget/);
});
test('an older backend without a breakdown gives no rows', () => assert.deepEqual(budgetView(plan({ breakdown: undefined })).rows, []));
