# ADR 0090: Decision register of 2026-11-19: what the owner settled and what is still open

Status: **Accepted** (2026-11-19). Issue 926. One place to look up, and to change, the decisions that had been waiting on the owner.

## Context

Many issues stopped on an owner question. On 2026-11-19 the owner answered "go with your pick" for all of them and asked that everything be recorded in ADRs, so any decision can be revisited. Where a decision has a build of its own, it has its own ADR (linked below); this ADR records the rest and lists what is still open. "Pick" means the recommendation made in the session; it was not a new analysis.

## Decisions taken

| Issue | Decision | Where it is recorded |
|---|---|---|
| 384 | The Kubernetes request counter is read through a governor socket verb (SPEC W-3: the page never runs Docker). The audit policy is not widened; the counter is labelled "nodes and pods only". | ADR 0087 |
| 213 | SSH traffic on the diagram lines is counted by a tiny counter inside each login and jump container, polled on a localhost port like `/sim/stats`. | ADR 0088 |
| 861 A3 | One definition of the services count: every drawn part with a reachability test (7/7 when all answer). | ADR 0089 |
| 860 | Drop `top`, `jira mode` and `workload run` from SPEC section 7 (done in this PR, with the test that checks section 7 against the CLI). SPEC section 7 stays the full verb list. | here |
| 388 | Keep the label `Slurm + K8s` and one kubectl line per k-login to the API box (built in PR 891). | here |
| 858 | `done` does not require `live`: the verified level is shown beside the status, never a gate. Export-profile items do not count as `client`; `client` is set only when a run against the monitoring client is stated in a PR. | here |
| 898 | Keep the `team-a` pod quota at 24 (already merged, issue 898). | here |

### Reasons and how to change each one made here

**860: remove the three verbs.**
- `top`: `get nodes`, `status` and the page already show resource use; a terminal `top` would be a third copy.
- `jira mode`: the mode is set in `sim.yaml`, and SPEC C-5 already proposes removing `dc` (issue 471, still waiting on the client's traffic). A runtime switch would need the mock to restart.
- `workload run`: `load on|off` and `submit` are the workload verbs (SPEC C-8).
- *To reverse:* add the verb in the module of its domain (`benchctl/commands/`), add it back to SPEC section 7, and add a row to `VERBS` in `benchctl/agents.py` if agents need it. `tests/test_agents_doc.py` then checks it exists.
- Keeping section 7 as the full list is enforced by `test_every_verb_spec_section_7_names_exists_in_the_cli`. The other option, pointing section 7 at `benchctl --help`, drops that check and lets the list drift.

**388: label and kubectl line.** The label tells the reader the k-login serves both schedulers; one kubectl line per k-login matches the real login nodes, which each hold their own kubeconfig. *To change:* both were built in PR 891; start from its diff.

**858: no gate.** Gating would mark most built items unfinished when only a live run is missing, hiding real progress. *To change:* `benchctl/progress.py` (`--unverified` already lists the unstated ones); making `done` need `live` is one check there plus a pass over `docs/progress.yaml`.

**898: quota 24.** The fixture needs room for the failing pods KWOK adds. *To change:* the `team-a` quota in the Kubernetes fixture under `k8s/` or `benchctl/k8s.py`; the live read tests in `tests/real/test_k8s_real.py` count pods against it.

## Still open

| Issue | Question | Options | Recommendation |
|---|---|---|---|
| 864 | Pin the Teleport CA instead of skipping TLS verification toward the local proxy. | (1) Pin the CA the Teleport auth service writes; (2) keep skipping on localhost, state it in the docs. | (1), but it is a security path: the owner decides and reviews. |
| 471 | C-5: the jira-mock `cloud` and `dc` modes. | Remove `dc` (the real Jira part covers it); build `cloud` only if the client's traffic shows v3 calls. | Wait for the client's `/sim/paths` and `/sim/traffic`. |
| 637 | Dark twins of the 12 terminal screenshots. | Build a generator; or recapture on a live cluster. | Deferred to a live capture session (owner decision 2026-11-18). |
| 861 B1, C2, E1 | Clearer names (B1), a hide-parts switch (C2), phone navigation (E1). | See the comments on issue 861. | None given: the owner needs a look at the page first. |
| 861 C1, C3 | Need a look at the real drawing. | | |
| 861 B4 | "Two items look selected": not reproduced. | | Close if it does not reappear. |

## Consequences

- SPEC section 7 is shorter and is now exactly the verbs that exist. No code is removed.
- The open table above is the to-do list for owner decisions; when one is settled, move its row up and link its ADR.
- None of this was exercised against the monitoring client or a live stack; these are documentation and test changes.
