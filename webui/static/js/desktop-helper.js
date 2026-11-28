// Opt-in page-title token (#983). A desktop helper that may see only this tab, not the whole browser, tells it from other
// tabs by a secret in the window title. The helper hands the token only to this page's origin (CORS), so another tab
// cannot copy it. Does nothing unless localStorage['workbench-desktop-helper'] is '1', and asks the page first whether it
// started with WORKBENCH_DESKTOP_HELPER=1 (the switch that adds the loopback origin to the CSP), so a page without it makes
// no blocked request and logs no console error. Never throws, never changes the title without a token.
(function () {
  var on = false;
  try { on = localStorage.getItem('workbench-desktop-helper') === '1'; } catch (e) { /* storage blocked: off */ }
  if (!on) return;
  var base = document.title;
  fetch('/api/desktop-helper')
    .then(function (r) { return r.ok ? r.json() : {}; })
    .then(function (j) {
      if (!j.on) return '';
      return fetch('http://127.0.0.1:47800/token', { headers: { 'X-Scope-Token': '1' } })
        .then(function (r) { return r.ok ? r.text() : ''; });
    })
    .then(function (t) { if (/^[A-Za-z0-9_-]{16,}$/.test(t)) document.title = base + ' ' + t; })
    .catch(function () { /* helper not running: nothing to do */ });
})();
