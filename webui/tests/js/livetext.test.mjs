import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { enc, setHTML, setText } from '../../static/js/util.js';

// an element that counts how often its text or markup is written: a live region is announced on every write
const el = () => { const e = { dataset: {}, writes: 0 }; let t = ''; let h = '';
  Object.defineProperty(e, 'textContent', { get: () => t, set: v => { t = v; e.writes++; } });
  Object.defineProperty(e, 'innerHTML', { get: () => h, set: v => { h = v; e.writes++; } }); return e; };

test('setText writes once for the same text, again when it changes', () => {
  const e = el(); setText(e, 'live'); setText(e, 'live'); setText(e, 'live'); assert.equal(e.writes, 1);
  setText(e, 'governor offline'); assert.equal(e.writes, 2);
});
test('setHTML writes once for the same markup', () => {
  const e = el(); setHTML(e, '<b>L0</b>'); setHTML(e, '<b>L0</b>'); assert.equal(e.writes, 1);
  setHTML(e, '<b>L1</b>'); assert.equal(e.writes, 2);
});
test('enc makes one safe path segment', () => assert.equal(enc('a/b ?#'), 'a%2Fb%20%3F%23'));
