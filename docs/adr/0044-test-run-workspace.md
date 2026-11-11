# ADR 0044: Test run workspace replaces the Scenarios drawer (redesign step 6b)

Status: accepted. Part of the UI redesign (epic 491); builds on ADR 0006, 0007, 0043 and the review items U7 and section 11.4.

## Context

A test had no home. The scenario (a drawer), its expectations (inside the drawer), Client's eye (a lens on the Topology) and the traffic
timeline were never visible together, and a report for a client bug had to be assembled by hand. U7: steps the page cannot run (it has
no Docker, SPEC W-3) were reported as skipped only after the run.

## Decision

- **Test run** is a workspace (`tab-run`, second in the nav). The drawer (`scndlg`) is removed. The run chip, the top-strip button and the
  Overview card select the tab; `scnOpen(true)` is kept as the one entry point.
- `GET /api/scenarios/{name}` returns the preview: steps as the engine will play them (ramps expanded), each with `page` (can the page run
  it) and `why`, the expectations with the same flag, the length and the CLI command. The flag comes from `scenario.page_support` and
  `scenario.expectation(host=False)`, the rules `_apply` uses, and a test checks the preview and a host-less run agree.
- Pure logic (`testrunmodel.js`): timeline rows joined by index with the run state, the verdict (a run with skips says so; a skip is never
  a pass), Client's eye rows and the report. `testrun.js` draws it; `scenarios.js` keeps the library and the polling.
- Client's eye uses the flow snapshot's counters, as a difference from Play. No counter means `n/a` and "not measured", never zero. The flow
  feed runs while this tab is visible and stops when the tab or page is hidden (W-11).
- The report (Markdown and JSON) is built in the browser from data the page already holds and is downloaded; the server stores nothing.

## Consequences

The Scenarios drawer's `sessionStorage` key is gone (the saved tab is enough). The report records what the counters showed, not which
endpoints the client used; capturing that is the focused wire (ADR 0041). Screens 3 and 4 (Topology polish, Cluster setup review) are 6c and 6d.
