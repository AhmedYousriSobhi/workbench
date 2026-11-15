# ADR 0070: Requests is the fifth zoom level, and a demo client feeds the mocks

Status: accepted by the owner on 2026-11-14 (issue 650).

## Context
The Traffic & Communication tab stacked the request analysis (the river) above the communication links, so a reader met both at once. A freshly started cluster also shows nothing in the river, because the mocks count only client requests and nothing calls them.

## Decision
- **Requests is a fifth zoom stop** (FLEET, CLUSTER, POOL, TRAFFIC, REQUESTS). In the Circuit view the Traffic tab shows one block at a time: the links at Traffic, the request analysis one zoom level deeper at Requests (`data-tl` on the tab, `S.trReq`). The rail, the `+`/`-` keys, Ctrl + scroll, **Back to the links** and Esc all move between them; leaving the tab always ends at the links. The classic (2.5D) diagram keeps both blocks, as before.
- **An opt-in demo client**, `benchctl load on --mocks [--mock-rate N]` (`images/login/mock_sim.py`, stdlib, started on the login node like the user simulator, stopped by `load off`): Poisson calls to jira-mock, bcm-mock and bmc-farm with its own User-Agent, paused by the governor's L1 flag. It is off by default so a real client under test is never mixed with background traffic, and nothing on the page is invented: the mocks count what arrives.

## Consequences
- The page's first view of traffic is the communication map; the request detail is a deliberate step in.
- `docs/manual/05-web-page.md`, `docs/manual/10-reference.md` and SPEC W-17c, C-8 describe both.
