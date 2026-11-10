// Test hook: exposes every module export and every field of the shared state as a window global, so the browser
// checks (scripts/ui_shots.py, scripts/doc_shots.py, tests/real/test_webui_browser.py) can drive the page by bare name.
// main.js loads it only when a test sets window.__WB_TEST first; a normal visit exposes nothing.
import { S } from './state.js';
import * as m_blast from './blast.js';
import * as m_cluster from './cluster.js';
import * as m_connections from './connections.js';
import * as m_diagram from './diagram.js';
import * as m_exportdlg from './exportdlg.js';
import * as m_flowfeed from './flowfeed.js';
import * as m_flowstate from './flowstate.js';
import * as m_focus from './focus.js';
import * as m_geometry from './geometry.js';
import * as m_governor from './governor.js';
import * as m_inspector from './inspector.js';
import * as m_lens from './lens.js';
import * as m_nhc from './nhc.js';
import * as m_node from './node.js';
import * as m_overview from './overview.js';
import * as m_plan from './plan.js';
import * as m_scenarios from './scenarios.js';
import * as m_shell from './shell.js';
import * as m_svg from './svg.js';
import * as m_traffic from './traffic.js';
import * as m_util from './util.js';

const expose = (name, desc) => { try { Object.defineProperty(window, name, { configurable: true, ...desc }); } catch (e) { console.warn('testhook: cannot expose', name); } };
for (const ns of [m_blast, m_cluster, m_connections, m_diagram, m_exportdlg, m_flowfeed, m_flowstate, m_focus, m_geometry, m_governor, m_inspector, m_lens, m_nhc, m_node, m_overview, m_plan, m_scenarios, m_shell, m_svg, m_traffic, m_util, ]) {
  for (const k of Object.keys(ns)) if (!/^init\d*$/.test(k)) expose(k, { get: () => ns[k] });
}
for (const k of Object.keys(S)) expose(k, { get: () => S[k], set: v => { S[k] = v; } });
