// canvas.css holds the redesign's tokens (docs/design/ui-redesign/DESIGN_HANDOFF.md). Contrast and animation rules are checked
// on the stylesheet itself, so a changed colour or a new dash pattern that breaks them fails here.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const css = readFileSync(fileURLToPath(new URL('../../static/canvas.css', import.meta.url)), 'utf8');
const tok = Object.fromEntries([...css.matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b/g)].map(m => [m[1], m[2]]));
const lum = hex => { const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(c => c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

test('the tokens are the brief\'s values with the owner\'s lighter ground (step 4)', () => {
  assert.deepEqual([tok['--cv-ground'], tok['--cv-surface'], tok['--cv-surface-2']], ['#111A26', '#16202E', '#1A2636']);
  assert.deepEqual([tok['--plane-access'], tok['--plane-control'], tok['--plane-k8s'], tok['--plane-data'], tok['--plane-monitoring'], tok['--plane-management']],
    ['#5CB8FF', '#E2E8F0', '#7C9CFF', '#C49BFF', '#4BE3A0', '#FFB347']);
  assert.deepEqual([tok['--cv-error'], tok['--cv-no-current']], ['#FF5D5D', '#51627A']);
});

test('text tokens reach 4.5:1 on the ground and both surfaces', () => {
  for (const t of ['--cv-text', '--cv-text-2', '--cv-text-muted', '--cv-attention', '--cv-error']) for (const bg of ['--cv-ground', '--cv-surface', '--cv-surface-2'])
    assert.ok(contrast(tok[t], tok[bg]) >= 4.5, `${t} on ${bg}: ${contrast(tok[t], tok[bg]).toFixed(2)}`);
});

test('every plane colour is readable as text and as a line on the ground', () => {
  for (const p of Object.keys(tok).filter(k => k.startsWith('--plane-'))) assert.ok(contrast(tok[p], tok['--cv-ground']) >= 4.5, `${p}: ${contrast(tok[p], tok['--cv-ground']).toFixed(2)}`);
});

test('each animated stroke moves by exactly its own dash period, so its loop never jumps', () => {
  const rules = [...css.matchAll(/\.(wire-pulse|wire-err)\s*\{([^}]*)\}/g)];
  assert.equal(rules.length, 2);
  for (const [, name, body] of rules) {
    const dash = body.match(/stroke-dasharray:\s*([\d.]+)\s+([\d.]+)/), period = body.match(/--period:\s*([\d.]+)/);
    assert.ok(dash && period, `${name} needs a dasharray and a --period`);
    assert.equal(Number(dash[1]) + Number(dash[2]), Number(period[1]), name);
  }
  assert.match(css, /@keyframes wire-flow\s*\{\s*to\s*\{\s*stroke-dashoffset:\s*calc\(var\(--period\) \* -1px\)/);
});

test('motion stops under reduced motion and while the tab is hidden', () => {
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*\.wire-pulse[^}]*animation:\s*none/);
  assert.match(css, /:root\[data-tab-hidden\][^{]*\.wire-pulse[^{]*\{\s*animation-play-state:\s*paused/);
});
