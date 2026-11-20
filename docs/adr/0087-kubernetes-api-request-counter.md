# ADR 0087: Kubernetes API request counter, per user, from the audit log through the governor

Status: **Accepted** (2026-11-19, owner decision: "go with the recommendation"). Issue 384 (re-scoped to per-user counts in the group 4 review, built with the Teleport view of issue 755). Builds on [ADR 0042](0042-on-demand-wire-capture.md) (the audit log and the governor's P5 capture), [ADR 0034](0034-kubernetes-kwok-first.md), [ADR 0083](0083-kubernetes-through-teleport.md) and [ADR 0084](0084-tsh-kube-production-practice.md). SPEC W-3 and W-18.

## Context

The page's Kubernetes API box showed one number: `apiserver_request_total`, the sum of every caller since the API server started (kubectl, the scheduler, the controllers, KWOK). It cannot say who asked, and it never falls, so it says nothing about the load now. The owner chose option 1 in issue 384: a per-user request counter built from the API audit log, one reader, shown as requests per minute on the box.

The audit log already exists (ADR 0042): the API server writes metadata only (user, verb, object, time, status; never a body) to its stdout. Reading it needs `docker logs`, and SPEC W-3 says the page never runs Docker and talks only to the governor socket. A request that comes through Teleport's Kubernetes Service reaches the API server as the `teleport` credential with the real user in `impersonatedUser` (ADR 0083), so "who" must be read from there first.

The audit policy (`AUDIT_POLICY`, `benchctl/k8s.py`) records only nodes, node status, pods, pod status, pod binding and leases. Any other request (secrets, configmaps, namespaces, CRDs) is not logged at all.

## Decision

1. **The page backend never reads Docker.** A new governor socket verb, `kube-audit` (optional `since`, unix seconds), is modelled on `capture-start|read|stop`: it is refused at L2 and above (`paused: memory pressure`), counts as class P5, is refused in dry-run and without `--workdir`, and returns at most 500 compact records `{id, t, user, verb, resource, object, code}`. A record never holds a body; nothing is written to disk. The governor runs `benchctl get kube-audit --since N` (read-only, as the owner of the checkout, like `get ticket-bot`), which reads the log with `docker logs --since` and looks back at most 10 minutes.
2. **The page counts.** `webui/kubeaudit.py` asks the governor at most every 5 seconds (15 seconds after a refusal), de-duplicates by audit id, keeps at most 1,000 records in memory, and reports requests per minute per user (the last 60 seconds) and the last 20 requests. The result rides on the existing flow snapshot (`kube` in `/api/flow`), only while the API server answers.
3. **The user is the real user.** `impersonatedUser.username` wins over `user.username`, so a request made with `tsh kube` counts for the Teleport user, not for `teleport`.
4. **The scope is not widened.** `AUDIT_POLICY` stays as it is, to keep the memory cost down. The counter is labelled "nodes and pods only" on the box, in the pop-up and in the docs. It is a count of the requests the policy records, not of all API traffic, and the older total (`apiserver_request_total`) stays as the "Seen" figure.
5. **Not measured is not zero.** With the governor down, at L2 and above, or the audit log unreadable, the page says "not measured" and the reason; it never shows 0.

## Options considered

| Option | Why not chosen |
|---|---|
| The page reads the audit log itself (`docker logs`) | Breaks SPEC W-3: the page would need Docker access. |
| Widen `AUDIT_POLICY` to every request, so the counter is complete | Each extra resource adds audit lines to the API server's stdout (Docker keeps up to 10 MB) and to the governor's and the page's read; the API server on a 16 GB workstation is a memory budget item. The owner chose to keep the cost down and to label the scope. |
| Keep the single `apiserver_request_total` figure | It has no user, which is what issue 384 asks for. It stays as the total. |
| Per-user counters from the metrics endpoint | `apiserver_request_total` has no user label; user-level metrics do not exist in kube-apiserver. |
| A running reader (stream) in the governor, like a capture | A permanent `docker logs -f` is a standing P5 workload; a polled bounded read costs nothing while nobody watches. |

## Consequences

- The box shows `N req/min · nodes and pods only` while the audit log is readable; the pop-up shows the per user list and the last requests. A user who only reads secrets, for example, does not appear: the label says so.
- The control plane's own traffic (scheduler, controllers, KWOK, kubelet-style node leases) appears as users such as `system:kube-scheduler`, as on a real cluster.
- The root governor service must be restarted after the merge to gain the verb (`sudo systemctl restart workbench-governor`); a user-mode governor restarts with `benchctl stop` then `benchctl start`. Until then the page says "not measured: unknown command".
- Every poll is one `docker logs --since` run by the governor, at most every 5 seconds per page, shared by all tabs, and none at L2 and above.

## How to change this later

- **Widen the scope** (count every request): edit `AUDIT_POLICY` in `benchctl/k8s.py` (the `rules`; `level: Metadata` for all, `omitStages: [RequestReceived]` stays; the file is rewritten at every `up`, so restart the API server). `KEEP` and `POLL_S` in `webui/kubeaudit.py` and `MAX_EVENTS` in `benchctl/kubeaudit.py` may need to rise; drop the `SCOPE` label in `webui/kubeaudit.py`, `circuit.js` (`k8s-api` case) and `inspector.js` (`kubeRows`), and the words "nodes and pods only" in this ADR, SPEC W-18 and `docs/manual/05-web-page.md`. Measure the API server's memory first (`benchctl measure`) and keep ADR 0042's note on the Docker log bound.
- **Read the log from the page** (reject W-3): not possible without changing SPEC W-3; it would need a decision record that replaces it.
- **Back to the single total**: remove the `kube` argument in `create_app` (`webui/app.py`, the `Flow(kube=...)` line); `Flow.poll` and `Flow.since` in `webui/flow.py` skip it when it is `None`; delete `kubeRows` in `inspector.js` and the `m:` text in `circuit.js`.
- **A permanent stream instead of a poll**: model it on `governor/capture.py` (a `Capture` ring fed by a child process); the verb would become `kube-audit-start|read|stop` in `governor/control.py` (`ALLOWED`, the argument checks) and `governor/core.py`.
