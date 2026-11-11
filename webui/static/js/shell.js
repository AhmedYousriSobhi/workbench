import { S } from './state.js';
import { $ } from './util.js';
import { buildFlow } from './diagram.js';
import { syncZoom } from './zoom.js';
import { startAnalysis, stopAnalysis } from './analysis.js';
import { clusterList, curLevel, enterCluster, paintLevelUi, setLevel } from './overview.js';
import { dockWide, paintReach } from './inspector.js';
import { paintConnections } from './connections.js';
import { closeFlow, openFlow } from './flowfeed.js';
import { paintTraffic } from './traffic.js';
import { paintHome } from './home.js';
import { loadCluster } from './cluster.js';
import { loadScenarios, poll, runChip } from './scenarios.js';
import { paintRunEye } from './testrun.js';

// Top menu: a disclosure (button + aria-expanded + list of buttons), closed by Escape, an outside click or choosing an item.
export function setMenu(open, refocus) {
  $('menu').hidden = !open; $('menubtn').setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) { if (innerWidth <= 860) $('menu').style.top = ($('menubtn').getBoundingClientRect().bottom) + 'px'; $('menu').querySelector('.menuitem').focus(); }
  else if (refocus) $('menubtn').focus();
}

// the node list beside the diagram folds away, so the diagram can have the whole width (remembered when storage allows)
export function setTree(open, remember = true) {
  $('nodeswrap').classList.toggle('treeoff', !open);
  if (remember) try { localStorage.setItem('workbench-tree', open ? 'open' : 'closed'); } catch (e) { /* not remembered */ }
}
export function setSub(v, remember = true) {  // All | Topology (diagram, list folded) | Nodes (list, diagram hidden)
  $('nodeswrap').classList.toggle('subnodes', v === 'nodes'); setTree(v === 'all' || v === 'nodes', false);
  document.querySelectorAll('#subseg [data-sub]').forEach(b => b.setAttribute('aria-pressed', b.dataset.sub === v));
  if (remember) try { localStorage.setItem('workbench-sub', v); } catch (e) { /* not remembered */ }
  if (v !== 'nodes') requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
}

export function setView(iso, remember) {  // 2.5D game style or the flat baseline (#320)
  $('trafficpanel').dataset.view = iso ? 'iso' : 'flat'; paintViewSeg();
  if (remember) try { localStorage.setItem('workbench-view', iso ? 'iso' : 'flat'); } catch (e) { /* not remembered */ }
}
export function setCircuit(on) {  // the Cluster level of the zoom (redesign step 3) or the diagram that was here before it
  if (S.circuit === on) return; S.circuit = on;
  if (on) { S.treeWas = !$('nodeswrap').classList.contains('treeoff'); setTree(false, false); } else if (S.treeWas) setTree(true, false);  // the circuit wants the width the node list uses
  try { localStorage.setItem('workbench-circuit', on ? '1' : '0'); } catch (e) { /* not remembered */ }
  $('nodeswrap').classList.toggle('circuit', on);
  if (curLevel() !== 'overview') buildFlow(); paintViewSeg();
}
export function paintViewSeg() {  // the view switcher (#324): one control for the style (2.5D, flat) and the level (diagram, overview)
  const ov = curLevel() === 'overview', iso = $('trafficpanel').dataset.view === 'iso', cv = !ov && S.circuit;
  for (const [id, on] of [['vw-iso', !cv], ['vw-cv', cv]]) $(id).setAttribute('aria-pressed', on);
  $('trafficpanel').toggleAttribute('data-circuit', cv); $('cvstack').hidden = !cv || !!S.pool; $('cvcap').hidden = !cv || !!S.pool;
}

// theme: dark first (Midnight) until a choice is remembered; Auto follows the system; storage may be refused
export const THEMES = ['auto', 'light', 'dark', 'midnight', 'dim'],
  THEME_ICON = { auto: '◐ Auto', light: '☀ Light', dark: '☾ Dark', midnight: '✦ Midnight', dim: '☁ Dim' };
export function setTheme(t) {
  if (t === 'auto') document.documentElement.removeAttribute('data-theme'); else document.documentElement.dataset.theme = t;
  $('theme').textContent = THEME_ICON[t]; $('theme').setAttribute('aria-label', `Colour theme: ${t}. Press to change.`); $('theme').dataset.t = t;
  try { localStorage.setItem('workbench-theme', t); } catch (e) { /* private mode: not remembered */ }
}
// tabs: the live traffic feed runs only while its tab and the page are visible
export const TABS = ['home', 'run', 'nodes', 'traffic', 'conn'];
export function selectTab(t, focus) {
  TABS.forEach(k => { const on = k === t; $('tab-' + k).hidden = !on; $('tb-' + k).setAttribute('aria-selected', on); $('tb-' + k).tabIndex = on ? 0 : -1; });
  $('main').dataset.tab = t; $('subseg').hidden = t !== 'nodes';  // the view choice belongs to Infrastructure
  if (focus) $('tb-' + t).focus();
  if ((t === 'run' || t === 'nodes' || t === 'traffic') && !document.hidden) openFlow(); else closeFlow();  // the run's Client's eye reads the same counters
  if (t === 'conn') paintConnections();
  if (t === 'home') paintHome();
  if (t === 'run') paintRunEye();
  if (t === 'traffic') paintTraffic();
  if (t === 'traffic' && !document.hidden) startAnalysis(); else stopAnalysis();
  syncZoom();
  try { sessionStorage.setItem('workbench-tab', t); } catch (e) { /* not remembered */ }
}

export function init() {
  $('menubtn').onclick = () => setMenu($('menu').hidden);
  $('menu').addEventListener('click', e => { if (e.target.closest('.menuitem')) setMenu(false); });  // the item's own handler opens its sheet
  document.addEventListener('click', e => { if (!$('menu').hidden && !e.target.closest('.menuwrap')) setMenu(false); });
  document.addEventListener('keydown', e => {
    if ($('menu').hidden) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setMenu(false, true); return; }
    const items = [...$('menu').querySelectorAll('.menuitem')], i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus(); }
    if (e.key === 'Tab' && !e.shiftKey && i === items.length - 1) setMenu(false);  // tabbing past the last item leaves the menu
  }, true);

}

export function init2() {
  document.querySelectorAll('#subseg [data-sub]').forEach(b => b.onclick = () => setSub(b.dataset.sub));
  const toDiagram = () => curLevel() === 'overview' ? enterCluster(clusterList()[0]) : 0;
  $('vw-iso').onclick = () => { setCircuit(false); setView(true, true); toDiagram(); };
  $('vw-cv').onclick = () => { setCircuit(true); toDiagram(); };
  let viewPref = null; try { viewPref = localStorage.getItem('workbench-view'); } catch (e) { /* default */ }
  setView(viewPref ? viewPref === 'iso' : !matchMedia('(prefers-reduced-motion: reduce), (max-width: 640px)').matches, false);  // default per device: flat under reduced motion and on a phone
  try { S.circuit = localStorage.getItem('workbench-circuit') !== '0'; } catch (e) { S.circuit = true; }  // the circuit is the way in; the classic diagram is a choice
  $('nodeswrap').classList.toggle('circuit', S.circuit);
  paintLevelUi();
  let treePref = null; try { treePref = localStorage.getItem('workbench-tree'); } catch (e) { /* default */ }
  if (treePref === 'closed') setTree(false, false);
  try { const sp = localStorage.getItem('workbench-sub'); if (sp === 'topo' || sp === 'nodes') setSub(sp, false); } catch (e) { /* default */ }  // the list sits beside or under the diagram; folding it away is remembered
  if (dockWide()) paintReach();
  $('theme').onclick = () => setTheme(THEMES[(THEMES.indexOf($('theme').dataset.t || 'auto') + 1) % THEMES.length]);
  let savedTheme = 'midnight'; try { savedTheme = localStorage.getItem('workbench-theme') || 'midnight'; } catch (e) { /* default */ }
  setTheme(THEMES.includes(savedTheme) ? savedTheme : 'midnight');

}

export function init3() {
  TABS.forEach((k, i) => {
    $('tb-' + k).onclick = () => selectTab(k);
    $('tb-' + k).onkeydown = e => { const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key]; if (d) { e.preventDefault(); selectTab(TABS[(i + d + TABS.length) % TABS.length], true); } };
  });
  // start
  loadCluster(); runChip(); loadScenarios(); poll();
  let savedTab = 'home'; try { savedTab = sessionStorage.getItem('workbench-tab') || 'home'; } catch (e) { /* default */ }
  selectTab(TABS.includes(savedTab) ? savedTab : 'home');
  // the live feed is only worth its CPU while the page is visible
  document.addEventListener('visibilitychange', () => { if (document.hidden || ($('tab-nodes').hidden && $('tab-traffic').hidden && $('tab-run').hidden)) closeFlow(); else openFlow(); if (document.hidden || $('tab-traffic').hidden) stopAnalysis(); else startAnalysis(); });

}
