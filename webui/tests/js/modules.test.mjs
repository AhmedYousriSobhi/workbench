// Every module links and evaluates on its own: a missing export, a bad import name or a load-time use of a binding that is not
// initialised yet (an import cycle) fails here, without a browser. main.js and testhook.js are the two that need a real page.
import './setup.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const dir = fileURLToPath(new URL('../../static/js/', import.meta.url));
const modules = readdirSync(dir).filter(f => f.endsWith('.js') && !['main.js', 'testhook.js'].includes(f));

test('there is a module per area, not one big file', () => assert.ok(modules.length >= 15, `found ${modules.length}`));

for (const m of modules) test(`${m} loads`, async () => {
  const ns = await import(new URL(m, `file://${dir}`));
  assert.ok(Object.keys(ns).length > 0, `${m} exports nothing`);
});

test('no two modules export the same name (the test hook puts them all on window)', async () => {
  const seen = new Map();
  for (const m of modules) for (const k of Object.keys(await import(new URL(m, `file://${dir}`)))) {
    if (/^init\d*$/.test(k)) continue;
    assert.ok(!seen.has(k), `${k} is exported by both ${seen.get(k)} and ${m}`);
    seen.set(k, m);
  }
});
