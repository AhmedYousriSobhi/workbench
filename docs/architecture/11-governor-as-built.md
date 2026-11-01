# The governor as built

[Architecture index](README.md) · [The governor (design)](05-governor.md)

This page is **derived from the code** (`governor/__main__.py`, `core.py`, `executors.py`), not a requirement.
[SPEC.md](../../SPEC.md) and [05 The governor](05-governor.md) say what it must do; this says what it does today. Where
they differ, SPEC.md wins and the code or this page gets fixed.

## Boot order (`main()`, the composition root)

`main()` builds every collaborator and hands them to `Governor`. It holds no policy, which is why a dependency graph
shows it bridging the socket, executors, ladder and core.

1. `MitigationPath()` is pre-allocated before memory is locked, so its pages are included (GV-8).
2. `discover_docker()` lists containers labelled `workbench.class=P<n>` into groups and cgroup paths.
3. A sampler (`/proc`, or a signals file or one cgroup in tests), then `Ladder(Thresholds)`.
4. `UserExecutor` (`--user-mode`, rootless) or `Executor` (root, cgroup files).
5. Optional: `Watchdog`, `Annotator` (Grafana), `effector` (runs `benchctl` for `scale`/`set-profile`).
6. `Governor(...)`, then the control socket server and the `/metrics` server, then `mlockall` (best effort without root).
7. Loop: `gov.step(now)`, sleep `--interval`.

## One control cycle (`Governor.step`)

```
sample -> ladder.update -> level changed?
   up:   reversible_first(plan(level, groups)) -> executor
   down: undo(prev, level, groups)             -> executor
then _watch (security scan) and _refresh (re-discover containers), both skipped from L3 up,
then render metrics, and pause Grafana annotations from L3 up
```

`Ladder` and `planner` are pure (no I/O). Only the executors touch Docker or cgroups. The socket and metrics servers see
`Governor` only through `handle`, `group_names` and the rendered metrics.

## Threads and the lock

The control socket serves each connection on its own thread; `scale`/`set-profile` run in a background thread. `Governor`
has **one lock**. `step()` holds it while it runs the plan, and `handle()` takes it for every command, `status` included.

### Measured: how long the lock is held

Real `Governor` and `UserExecutor` driven in-process, 12 throwaway `busybox` containers (not labelled, so the installed
governor ignores them), a second thread calling `handle("status")` while the plan runs. One run on the 16 GB development
workstation, so read the numbers as an order of magnitude.

| Containers | L3 freeze (`docker pause`) | L4 stop (`kill` then `stop -t 2`) | Longest `status` wait |
|---|---|---|---|
| 4 | 0.11 s | 0.67 s | 0.64 s |
| 12 | 0.33 s | 2.08 s | 2.05 s |

About 28 ms per pause and 175 ms per stop, linear in the number of containers, no action failures. The wait equals the
whole plan, so a profile with dozens of containers would block a socket request for several seconds at L4 (extrapolated,
not measured). The loop also cannot re-sample while the plan runs.

**Not measured:** the downward path (`undo`, `docker unpause`). The harness never stepped the ladder back down, so there
is no thaw timing. Root mode writes cgroup files and does not fork docker, so it was not measured either.

## Follow-up candidates

None of these is a reproduced bug.

| Observation | Why it matters | Cheap fix |
|---|---|---|
| `effector` (the `runuser` and ownership logic) is a closure inside `main()` | Hard to unit-test | Move it to a module-level function |
| `Governor.__init__` takes 10 parameters, the later ones passed positionally | Reordering them fails silently | Pass keyword arguments |
| `status` reports `mode` with `isinstance(executor, UserExecutor)` | Implicit, correct today | Ask the executor for its mode |
| The plan runs under the lock (table above) | A command waits for the whole plan | Decide first whether it matters: a freeze or stop request that waits is also serialised, which may be wanted |
