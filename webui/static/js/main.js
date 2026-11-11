// Entry point: every module is evaluated first, then each module's wiring runs in the order the single script used to run it.
if (window.__WB_TEST) await import('./testhook.js');  // browser checks only, see testhook.js
import { init as init_governor, init2 as init2_governor } from './governor.js';
import { init as init_plan, init2 as init2_plan, init3 as init3_plan } from './plan.js';
import { init as init_scenarios, init2 as init2_scenarios } from './scenarios.js';
import { init as init_shell, init2 as init2_shell, init3 as init3_shell } from './shell.js';
import { init as init_nhc } from './nhc.js';
import { init as init_exportdlg } from './exportdlg.js';
import { init as init_node } from './node.js';
import { init as init_inspector } from './inspector.js';
import { init as init_overview } from './overview.js';
import { init as init_focus } from './focus.js';
import { init as init_lens } from './lens.js';
import { init as init_diagram } from './diagram.js';
import { init as init_flowfeed } from './flowfeed.js';
import { init as init_traffic } from './traffic.js';
import { init as init_wire } from './wire.js';

init_governor();
init_plan();
init2_governor();
init_scenarios();
init2_plan();
init_shell();
init_nhc();
init_exportdlg();
init3_plan();
init_node();
init_inspector();
init_overview();
init_focus();
init_lens();
init_diagram();
init_flowfeed();
init2_shell();
init2_scenarios();
init_traffic();
init3_shell();
init_wire();
