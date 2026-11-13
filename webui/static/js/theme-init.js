// Applies the saved colour theme before first paint, so a reload does not flash the default (colour system, issue 647).
// A classic script with no imports, loaded in <head> before the stylesheets (no defer, async or module: those run after
// first paint). Keep the storage key and the default in step with shell.js; tests/test_themeinit.py checks both.
(function () {
  var t = 'midnight';
  try { t = localStorage.getItem('workbench-theme') || 'midnight'; } catch (e) { /* storage blocked: the default */ }
  if (t === 'auto') return;      // Auto follows the system: no data-theme
  if (['light', 'dark', 'midnight', 'dim'].indexOf(t) < 0) t = 'midnight';
  document.documentElement.dataset.theme = t;
})();
