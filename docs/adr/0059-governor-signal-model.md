# ADR 0059: Governor signals: swap storm, memory.events, compressed swap

Status: accepted (issue 463; the PSI trigger, pre-opened files and derived thresholds of issue 464 are added here when they land)

## Context
GV-2 listed signals the governor sampled or ignored: the swap-out rate was computed and never used by the ladder, the
per-cgroup `memory.events` were never read, and `MemAvailable` ignored that zswap and zram hold their pages in RAM.

## Decision
- **Swap storm.** `pswpout` above `swap_pages_s` (default 2000 pages/s, about 8 MB/s) for `swap_sustain_s` (10 s) adds one
  level to what PSI and headroom ask for: calm becomes L2 (throttle), L2 becomes L3 (freeze and reclaim). It never goes above
  L3 and never changes L4 or L5, which stay decided by headroom and PSI full. `swap_pages_s: 0` turns it off. Both are
  `--thresholds` keys.
- **memory.events.** Every 5 s the governor reads `memory.events` of each container it governs. The totals of `high`, `oom` and
  `oom_kill` and the five containers with most `high` are in `status`; each container's counters are in `/metrics`
  (`workbench_container_memory_events`). A rise in `oom_kill` or `oom` is an audit event (`OOM KILL: NAME (+N)`). The first
  read is a baseline, so old counts raise nothing. Not read from L3 up: reading allocates (GV-8).
- **Compressed swap.** The Zswap pool (`/proc/meminfo`) and every zram device (`mem_used_total`) are taken off
  `mem_available`, which makes the ladder and admission slightly more cautious. This is a conservative choice, not a
  measured one: the kernel already counts the pools as used, so the subtraction is the margin for the pages that
  reclaim will still compress. It is shown as `compressed_swap` in `status` and `workbench_host_compressed_swap_bytes`.
  With neither enabled nothing changes.

## Consequences
- The 2000 pages/s and 10 s defaults are starting values, argued and unit-tested, not tuned on a loaded host. Tune with `--thresholds`.
- The signals file (`--signals-file`) can set `swap_out_rate` and `compressed_swap` to drive the ladder in tests.
