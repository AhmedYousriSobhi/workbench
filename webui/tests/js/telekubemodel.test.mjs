import test from 'node:test';
import assert from 'node:assert/strict';
import { kubeRows } from '../../static/js/telekubemodel.js';

test('hidden, or a notice only, when there is nothing to show', () => {
  assert.deepEqual(kubeRows(null), []);
  assert.deepEqual(kubeRows({ shown: false }), []);
  assert.deepEqual(kubeRows({ shown: true, notice: 'Teleport is not answering <b>' }), [['Kubernetes', 'Teleport is not answering &lt;b&gt;']]);
});

test('a counter that is absent reads "not measured", a zero reads 0', () => {
  const rows = kubeRows({ shown: true, counters: { requests: 12, exec: 0, portforward: null }, events: [] });
  assert.deepEqual(rows.slice(0, 3), [['Kubernetes requests', '12'], ['Exec sessions', '0'], ['Port-forward sessions', 'not measured']]);
  assert.deepEqual(rows[3], ['Recent kube calls', 'none yet']);
});

test('an event shows user, verb, resource and the code with a word, and escapes what Teleport wrote', () => {
  const rows = kubeRows({ shown: true, counters: {}, events: [
    { user: 'alice', verb: 'GET', resource: 'nodes/kcn001', code: 200 }, { user: 'b<ob>', verb: 'DELETE', resource: 'pods/x in team-a', code: 403 }] });
  const html = rows.at(-1)[1];
  assert.match(html, /<b>alice<\/b> GET nodes\/kcn001 <span class="kube-code" data-outcome="ok">200 ok<\/span>/);
  assert.match(html, /b&lt;ob&gt;.*403 refused/);
});
