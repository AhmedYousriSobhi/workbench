# ADR 0089: One definition of the services count on the page

Status: **Accepted** (2026-11-19, owner decision: "go with the recommendation"). Issue 927, item A3 of the UX review in issue 861. Builds on [ADR 0022](0022-infrastructure-architecture-and-teleport-in-the-page.md) (the drawn parts) and SPEC W-10, W-12.

## Context

The review of the page (issue 861, A3) found the same count in two places with different numbers: the top strip said `Services 7/7`, the Overview tile said `5 of 5 services up`.

The cause was two sources and two lists:

- The **snapshot** (`/api/cluster`, `webui/cluster.py` `snapshot()`) holds a `services` map, one reachability probe per part: the four always-built mocks (`SERVICES`), plus Teleport, the real Jira, the DCGM and real-GPU exporters, the jump host, the controller and the Kubernetes API when they are built. The strip chip, the Services list heading, the header line and the Needs attention list read it.
- The **drawn Services column** (`svcList()` in `flowstate.js`) does not hold the jump host and the controller (they are drawn in the Access layer), and its reachability came from the traffic feed (`flowSnap.reachable`). The Overview tiles, cards and the clusters table counted that list.

Both were "services", neither was wrong, and the page never said which parts a count covers.

## Decision

**A service is every part the page draws that has a reachability test, and the count is the same everywhere:** 7/7 when all seven drawn parts answer.

One pure function, `servicesCount(services)` in `webui/static/js/servicecount.js`, returns `{ up, all, down, stackDown, ratio: '7/7', words: '7 of 7' }` from the snapshot's `services` map. Every place that shows a services count uses it and nothing else: the strip chip (`governor.js`), the Overview tiles, cards, table and the per-part marks (`overview.js`, `cluster.js`), the Services list heading (`cluster.js`), the header line and the Needs attention entries (`homemodel.js`). The snapshot map is the one source: the drawn parts and the probed parts are the same set (`tests/test_services_count.py` ties the always-drawn base list to `SERVICES`, and fails when a module counts without the function).

A stack with parts and none answering is "stack down" (W-10), from the same function.

## Options considered

1. **Count only the drawn Services column** (what the tile did: 5 of 5). Not chosen: it leaves out the jump host and the controller, which are parts with their own up/down state; "all services up" could hide a dead controller.
2. **Count only parts with a request counter** (the mocks and Teleport; the traffic feed's `STATS` and `COUNTERS`). Not chosen: the real Jira, the Kubernetes API and the exporters are services the client calls, and would vanish from the number.
3. **Two counts, one per layer** (Access and Services). Not chosen: it multiplies the numbers a reader must reconcile, the opposite of the finding; the per-part state is already one click away in the list.

## Consequences

- The strip, tile, header line and list read the same, for example `Services 7/7`, `7 of 7 services up`.
- With a jump host built and down, the Overview tile now says 6 of 7 (it said 5 of 5 before).
- Overview pill marks now follow the snapshot, so a mark can no longer disagree with the count beside it.
- The Services list heading still names the parts one by one; nothing about a single part's box changed.
- Not run: live page, the browser tests (the `ui_shots` check was added, owner runs it), the monitoring client.

## How to change this later

- **To option 1** (drawn Services column only): in `overview.js` `clusterFacts()`, pass `Object.fromEntries(svcs.map(s => [s[0], !!sc.services?.[s[0]]]))` instead of `sc.services` to `servicesCount`; do the same in `governor.js` `paintHealth` and `cluster.js`/`homemodel.js` (filter the map to `svcList()` keys, which needs the filter moved into `servicecount.js` as a second argument). Update the two tests and the `ui_shots.py` `check_services_count` figures.
- **To option 2** (counter parts only): filter the map in `servicesCount` to the keys of `STATS` and `COUNTERS` in `webui/flow.py` (the server could send the list in `/api/cluster`, so the page does not copy it). Update `webui/tests/js/servicecount.test.mjs`.
- **To option 3** (one count per layer): add a `layer` argument to `servicesCount`, call it twice in `governor.js` (two chips) and in `overview.js` (two tiles); update the manual row "Services count" in `docs/manual/05-web-page.md`.

## Other items of issue 861: open, owner decision pending

Not changed by this ADR. Each stays open until the owner decides.

- **B1 (names):** the tab "Topology", its panel "Infrastructure architecture" and the sheet "Cluster architecture" are three names for nearly one thing. Option known: one name for the diagram in tab, panel and docs, and rename the sheet to what it does ("Build cluster" or "Configure cluster"). Needs the owner's wording; SPEC W-15 and the manual use the current names.
- **C1 (classic view labels about 7 px):** options: fold the node list by default when the classic view is on, or raise the label size and move secondary lines (address, counts) to the tooltip. Needs a look at the real drawing; contrast 4.5:1 for small grey text (W-9).
- **C2 (Circuit view, half the canvas is grayed parts the cluster does not have):** W-17d says a missing part is grayed, never dropped. Options: keep as is, or add a "Hide parts not in this cluster" switch, off by default, that keeps the fixed positions (touches W-17d).
- **C3 (wire labels overlap and are cut, `admin SSH`, `ssh -J`):** see issue 388; to be done together with C1.
- **E1 (phone, content starts about 700 px down):** options: status chips in one scrolling row or behind one summary chip; navigation as a bottom bar or one menu button; tiles only on Overview (B2, done).
