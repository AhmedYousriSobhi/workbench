# ADR 0077: scheduling policy objects and API throttling on the Kubernetes part

Status: accepted by the owner on 2026-11-14 (issue 548, epic 557). Built; the live run is pending.

## Context
A real GPU cluster has priority classes, quotas and disruption budgets, and its API server can throttle a client with API Priority and Fairness (APF). A monitoring client meets all four as real API behaviour (a preempted pod, a drain that will not finish, a 403 on quota, a 429). The Kubernetes part had none of them.

## Decision
- `k8s/fixtures/60-policy.yaml` (applied by `up`): PriorityClasses `inference` (2000), `training` (1000) and `best-effort` (-100, never preempts); a ResourceQuota `gpu-claims` in `team-a` of two DRA devices of the simulated driver (ADR 0076; `dra-train` holds one); a Deployment `inference` (two replicas, one per GPU node by a spread constraint) with a PodDisruptionBudget `minAvailable: 2`, so `kubectl drain` of a node that holds a replica blocks.
- `k8s/scenario/70-apf.yaml` (not in `k8s/fixtures/`, which differs from the issue text): a PriorityLevelConfiguration with one concurrency share (about three seats, `Reject`, no queue) and a FlowSchema that matches only the user `viewer`. `up` applies every file in `k8s/fixtures/`, and this one changes how the API server treats a user, so only scenario `k8s-api-throttled` applies it and removes it. The metrics-only user and every other caller are not matched, and RBAC is untouched.
- Scenario steps: `pod` takes `priority_class` and `node`; new `drain: {node, timeout}` (cordon and evict through the eviction API; a drain the budget blocks times out and is not a failed step) and `remove: [path under k8s/]`. New expectation `k8s.pods_on_node: {node, namespace, selector, min}`.
- Scenarios `k8s-preempt` (SC-38: two best-effort pods of 3 GPUs and an inference pod of 4 on `kgpu001`), `k8s-drain-blocked` (SC-39) and `k8s-api-throttled` (SC-40).

## Not verified yet
`./scripts/verify.sh --unit k8s`: that the scheduler preempts and records `Preempted`, that the drain is refused with the budget named, that the quota rejects the second claim (the quota resource name follows the DRA documentation), and that a burst of 300 concurrent requests as `viewer` gets a 429. The APF seat count is a calculation (600 seats shared by about 245 shares), so a 429 under a light burst is likely but not guaranteed; the live test uses a heavy one.

## Consequences
- Two more pods in `team-a` (the pod quota there is 8; `web` 2, `gpu-train`, `dra-train`, `inference` 2 are 6). Scenario pods go to `team-b`.
- `gpu-train` keeps the default priority (0): a PriorityClass cannot be used by a pod applied in the same pass.
- APF stays off until a scenario turns it on.
