# The governor

[Architecture index](README.md) · [Project overview](../../README.md)

## 6. The governor (outside monitoring and control)

### 6.1 Placement and protection
- Runs **on the host**, not in a container or VM: a systemd service with `OOMScoreAdjust=-900`, a `MemoryLow=` reservation, `Restart=always`, and its own slice. It talks to Docker (API), libvirt (`virsh`), systemd and cgroupfs.
- Compose services join per-priority slices with `cgroup_parent: workbench-<class>.slice` (needs Docker's systemd cgroup driver on cgroup v2). Resources it can't parent (kind/k3d nodes, the VM) are addressed by name through the Docker API or libvirt `memtune`.

### 6.1a Race-condition safeguards
Above roughly 30% PSI the kernel struggles to hand out new memory, so the governor must not need any:
- Lock all pages at startup (`mlockall(MCL_CURRENT | MCL_FUTURE)`, e.g. `os.mlockall()`), so it is never swapped out.
- Pre-allocate the L3/L4 execution path (open cgroup file handles, Docker/libvirt clients, command buffers) at startup; mitigations then perform no dynamic allocation.
- Listen on a Unix socket (`/run/workbench/gov.sock`) for commands from the unprivileged Web UI backend ([Web UI](06-ux-and-scenarios.md#71-web-ui)).

### 6.2 Signals
- `MemAvailable`, swap-in/out rate, and **PSI** (`/proc/pressure/memory`, per-cgroup `memory.pressure`): PSI measures time lost to memory stalls, which is a better "about to hurt" signal than raw usage.
- Per-group `memory.current`, `memory.events` (`high`/`max`/`oom_kill` counters) vs declared budgets.
- CPU and IO PSI as secondary signals.

### 6.3 Escalation ladder

| Level | Trigger (starting values, tune them) | Action | Targets |
|---|---|---|---|
| L0 normal | PSI some avg10 < 5% and MemAvailable > 2.5 GB | none | n/a |
| L1 warn | PSI some > 10% or MemAvailable < 2.0 GB | emit event/annotation; stop new job/ticket arrivals | P5 |
| L2 throttle | PSI some > 20% for 10 s | lower `memory.high` on low classes; slow `user-sim`; scale slurmd count down | P5, P4 |
| L3 freeze + reclaim | PSI some > 30% for 10 s | `docker pause` / `cgroup.freeze`, then `memory.reclaim` (kernel 5.19+) to push pages to zram/swap | P6, P5, P4 |
| L4 stop | PSI **full** > 5% or MemAvailable < 1.0 GB | `docker stop`; `virsh managedsave` the VM (frees its RAM) | P6, then P5, P4, P3 |
| L5 emergency | still degrading after L4 | stop the whole profile except P0/P1 | all but P0/P1 |

Key nuance: **pausing/freezing does not release RAM**, it only stops CPU use. Freeze is a cheap, instantly reversible step; actual relief comes from `memory.reclaim` (to compressed swap) or a real stop/save.

### 6.4 Behavior rules
- **Hysteresis:** escalate fast, de-escalate slowly (calm for 60 s before each step down), minimum dwell time per level.
- **Priority classes:** P0 governor/panel, P1 core + app, P2 mocks, P3 K8s, P4 compute, P5 load, P6 VM/Jira DC. Lowest priority goes first.
- **Reversible first, destructive last,** and every action is logged and visible in Grafana as an annotation.
- **Admission control:** `benchctl up` and scenarios that start things must ask the governor first.
- **Backstops:** kernel `memory.max` per container as the hard cap; **systemd-oomd** on a sacrificial slice (`ManagedOOMMemoryPressure=kill`) so the kernel OOM killer is the last resort, not the first. oomd needs cgroup v2 and PSI.
- **Dry-run mode** (`governor --dry-run`) logs what it would do, for threshold tuning.

### 6.5 Host tuning (one-time, scripted by `benchctl doctor`)
- Confirm cgroup v2 and PSI (`cat /proc/pressure/memory`).
- **zram** swap (zstd, about 25-50% of RAM), low-ish `vm.swappiness`, THP set to `madvise`.
- Docker: `max-size` log rotation, `--pids-limit`, per-container `--memory` and `--memory-reservation`.
- Enable KSM only if you use several similar VMs (it merges QEMU memory; it won't help ordinary containers).
- Slim defaults: MariaDB `innodb_buffer_pool_size` small, Grafana analytics/plugins off, Prometheus retention short.
