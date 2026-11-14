<p align="center"><img src="../assets/art/07-governor-hero.svg" alt="The governor badge beside a ladder of four levels, from normal to stop, with the marks growing more serious" width="720"></p>

# 7. The governor

**In 30 seconds:** the governor is the bouncer. It watches your RAM from outside the cluster and, when memory gets tight, pauses the least important containers first and stops them only if that is not enough. You rarely need to touch it.

## Do it

```bash
./scripts/benchctl status          # the level, the mode, memory, recent actions
```

Level L0 means all is well. Anything higher means the governor is protecting your machine.

The governor keeps the host alive. It runs **on the host, outside everything it may stop**, watches memory, and escalates
through a ladder, acting on the lowest-priority containers first and on reversible actions before destructive ones.

## Two modes

| | Rootless (default) | Root service (optional) |
|---|---|---|
| Started by | `./scripts/benchctl start`, as you | `sudo ./scripts/install-governor.sh`, at boot |
| Needs sudo | no | once, to install |
| Acts by | `docker pause`, `docker kill`/`stop` | cgroup files |
| Throttle (`memory.high`) and reclaim | no (skipped, logged once) | yes |
| Own memory locked and OOM-shielded | best effort | yes (SPEC GV-8) |
| `systemd-oomd` backstop on `workbench.slice` (GV-10) | no | only with `--with-oomd` (off by default) |

When the root service is running it wins. Details and the reasons: [ADR 0010](../adr/0010-rootless-governor-mode.md).

## The ladder

| Level | Trigger (defaults) | What happens |
|---|---|---|
| L0 normal | pressure under 5% and more than 2.5 GB available | nothing |
| L1 warn | pressure over 10% or under 2.0 GB available | event and annotation; new job and ticket arrivals stop (P5) |
| L2 throttle | pressure over 20% for 10 s | lower `memory.high` on low classes (root only); `up` and `scale` refused |
| L3 freeze | pressure over 30% for 10 s | pause P6, P5, P4 containers; animation stops on the web page |
| L4 stop | full-stall pressure over 5% or under 1.0 GB available | stop P6, then P5, P4, P3 |
| L5 emergency | still degrading after L4 | stop everything except P0 and P1 |

It escalates at once and de-escalates one level at a time after 60 s calm (minimum 10 s per level). Pausing frees no RAM;
it only stops growth. Relief comes from stopping. Thresholds can be overridden with `--thresholds` (JSON). The memory thresholds default from `host.reserve_gb` and `safety_floor_gb` in `sim.yaml`; a sustained swap-out storm (`swap_pages_s`, `swap_sustain_s`) adds one level up to L3; the governor wakes on a kernel PSI stall trigger where the kernel allows it (`--no-psi-trigger` to poll only). See ADR 0059.

## Look at it

```bash
./scripts/benchctl status          # level, mode (user or root), memory, groups, security findings, recent actions
```

The same data is on the web page (the Governor panel from the top strip, and the log), in Grafana as annotations on every action, and in Prometheus
(`workbench_governor_level`, `workbench_host_mem_available_bytes`). The governor's own metrics come from its own endpoint, so
they survive when Prometheus is stopped.

## Admission

`benchctl up` and `scale` ask the governor first. At L2 and above, or if the start would breach the safety floor, it refuses
and says why. **That is a decision, not a bug.** Close a browser or the IDE, check `benchctl status`, and retry.

## Security watchdog

Every 15 s the governor flags containers that are privileged, have added capabilities, run unconfined, share host
namespaces or mount the Docker socket, plus missing memory limits and limit drift. It reports; it does not fix. Findings
appear in `benchctl status` and the audit log. The one accepted exception is `slurmrestd`'s two disabled unshare steps
([ADR 0004](../adr/0004-slurmrestd-unshare-stays-disabled.md)).

## Dry run

```bash
python -m governor --dry-run --once      # sample once and log what it would do
```

Use `--signals-file F.json` to drive the ladder from a file when tuning. The full flag list is in `python -m governor --help`.
The design is in [the architecture](../architecture/05-governor.md) and what the code does today in
[the governor as built](../architecture/11-governor-as-built.md).

## Known limit

While the governor executes a plan, `status` waits for the plan (about 28 ms per pause and 175 ms per stop per container,
measured). With few containers this is invisible; tracked in issue #53.

## Under the hood

> [!HOOD]
> The ladder is pure logic with no I/O: the planner turns a level into actions, cumulative, so L4 also does what L3 does.
>
> | Level | Acts on | Verb |
> |---|---|---|
> | L1 | everything | notify, stop new arrivals |
> | L2 | P5, P4 | throttle (`memory.high`, root service only) |
> | L3 | P6, P5, P4 | freeze, then reclaim |
> | L4 | P6, P5, P4, P3 | stop (VMs are saved) |
> | L5 | P2 to P6 | stop everything left except P0 and P1 |
>
> Reversible actions run before destructive ones. Freeze, throttle and stop-arrivals are undone as the level falls; stops are never undone by the governor.
> Escalation is immediate; each step down needs 60 s of calm and at least 10 s in the level. L2 and L3 pressure must hold 10 s, the L4 stall 5 s, and an L4 condition that lasts 30 s becomes L5.
> User mode acts only by `docker pause` and `docker stop`; the root service adds cgroup throttle and reclaim and locks its own memory (ADR 0010, SPEC GV-8).

Source: `governor/ladder.py` (`Thresholds`, `Ladder`), `governor/planner.py` (`plan`, `reversible_first`), [ADR 0010](../adr/0010-rootless-governor-mode.md), [ADR 0059](../adr/0059-governor-signal-model.md), SPEC GV-3, GV-4, GV-8

## Tips

> [!TIP]
> Pausing frees no RAM; it only stops growth. If you see a node frozen, the cure is more free memory, not a restart.

## If it goes wrong

| Symptom | Try |
|---|---|
| `up` is refused | the governor is at L2 or above: close a browser or the IDE, check `benchctl status`, retry |
| A node is frozen | it thaws by itself after 60 s calm |
| The web page says the governor is down | `./scripts/benchctl start` (no sudo) |

## Learn more

- [The governor as built](../architecture/11-governor-as-built.md) and [the design](../architecture/05-governor.md)
- [Troubleshooting](09-troubleshooting.md)
- [Memory guide](../guides/memory.md)
