// canvas.css holds the redesign's tokens (docs/design/ui-redesign/DESIGN_HANDOFF.md). Contrast and animation rules are checked
// on the stylesheet itself, so a changed colour or a new dash pattern that breaks them fails here.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const css = readFileSync(fileURLToPath(new URL('../../static/canvas.css', import.meta.url)), 'utf8');
const tokcss = readFileSync(fileURLToPath(new URL('../../static/tokens.css', import.meta.url)), 'utf8');
// A canvas follows the page theme (issue 649): the --cv-* names are aliases written into each theme's block of tokens.css.
const BLOCKS = { light: /:root, :root\[data-theme=light\] \{([^}]*)\}/, dark: /:root\[data-theme=dark\] \{([^}]*)\}/, midnight: /:root\[data-theme=midnight\] \{([^}]*)\}/, dim: /:root\[data-theme=dim\] \{([^}]*)\}/ };
const themes = Object.fromEntries(Object.entries(BLOCKS).map(([n, re]) => [n, Object.fromEntries([...tokcss.match(re)[1].matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b/g)].map(m => [m[1], m[2]]))]));
const lum = hex => { const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(c => c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

test('every theme has the six plane colours and the canvas names; the dark theme keeps the planned plane values', () => {
  for (const [name, tok] of Object.entries(themes))
    for (const k of ['--cv-ground', '--cv-surface', '--cv-surface-2', '--cv-text', '--plane-access', '--plane-control', '--plane-k8s', '--plane-data', '--plane-monitoring', '--plane-management']) assert.ok(tok[k], `${name} lacks ${k}`);
  const d = themes.dark;
  assert.deepEqual([d['--plane-access'], d['--plane-control'], d['--plane-k8s'], d['--plane-data'], d['--plane-monitoring'], d['--plane-management']],
    ['#5cb8ff', '#e2e8f0', '#8ea2ff', '#c49bff', '#a6d96a', '#f48fb1']);
});

test('text tokens reach 4.5:1 on the ground and both surfaces, in every theme', () => {
  for (const [name, tok] of Object.entries(themes)) for (const t of ['--cv-text', '--cv-text-2', '--cv-text-muted', '--cv-error']) for (const bg of ['--cv-ground', '--cv-surface', '--cv-surface-2'])
    assert.ok(contrast(tok[t], tok[bg]) >= 4.5, `${name} ${t} on ${bg}: ${contrast(tok[t], tok[bg]).toFixed(2)}`);
});

test('every plane colour and the attention colour are readable as a line on the ground and the surface, in every theme', () => {
  for (const [name, tok] of Object.entries(themes)) for (const p of Object.keys(tok).filter(k => k.startsWith('--plane-') && !k.endsWith('-dash')).concat('--cv-attention')) for (const bg of ['--cv-ground', '--cv-surface'])
    assert.ok(contrast(tok[p], tok[bg]) >= 3, `${name} ${p} on ${bg}: ${contrast(tok[p], tok[bg]).toFixed(2)}`);
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
