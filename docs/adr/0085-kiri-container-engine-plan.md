# ADR 0085: Kiri as a Docker Engine alternative: spike first, no switch now

Status: **Proposed** (2026-11-15), awaiting the owner's decision. Issue 800. Research only: nothing was installed or run. The capability matrix, project facts and sources are in the research note [Kiri as a Docker Engine alternative](../research/kiri-container-engine.md). Touches [ADR 0010](0010-rootless-governor-mode.md), [ADR 0074](0074-slurm-job-accounting-and-slurms-own-metrics.md), [ADR 0081](0081-config-lab-validation.md) and SPEC P-2.

## Context

The owner asked to look at Kiri "instead of Docker", knowing it would be a large change, and to plan it in an ADR. The lead was a Show HN post claiming "18 ms Docker runs".

**It is the right project.** The post's submitter owns [github.com/libracoder/kiri](https://github.com/libracoder/kiri): Rust, Apache-2.0, v0.2.1. Other projects named Kiri are a screenshot tool, a ports viewer and a desktop shell, none a container engine.

**What Kiri is.** A replacement for the Docker *daemon*, not for the `docker` CLI. It serves the Docker Engine API (version 1.47) on its own socket. The real `docker` CLI, Compose and buildx talk to it. On Linux it runs on the host kernel as a root systemd service; on macOS and Windows it runs a small VM.

That shapes the plan. workbench would keep `docker compose`, the generated compose file and almost every `docker` call. The change is in what sits under the socket: cgroup layout, networking, builds and security defaults.

**What workbench uses.** About 300 lines in 83 files outside the unit tests call Docker (research note, section 3): `benchctl` 29 files, the governor 6, live tests 43. The governor talks to the Engine API directly. The compose generator relies on `cgroup_parent` slices, fixed IPs, `SYS_PTRACE`, healthchecks and `tmpfs` sizes.

## What Kiri offers, in short

| Area | Verdict | Main reason |
|---|---|---|
| API, CLI, Compose, lifecycle | Good | Docker API served; pause uses cgroup v2 freeze; exec, cp, logs, stats, labels, healthchecks, restart policies present |
| Memory limits | Good, one difference | `memory.max` set; swap always off per container (Docker allows it) |
| Priority slices (`cgroup_parent`) | **Missing** | Ignored silently (Kiri issue 213): breaks P0-P6 classes and the oomd backstop |
| cgroup layout | **Different** | `<kiri.service>/kiri/<id>`, not `docker-<id>.scope`: root governor and `measure` need a resolver |
| Networking | Mostly | Subnets, fixed IPs, DNS, 127.0.0.1 ports; `--internal` only drops the default route; open Linux bugs (228, 247, 294) |
| Builds | **Blocked on Linux** | Every `docker build` fails on Linux today (issue 227); `FROM` a local image unknown |
| Security | Weaker on Ubuntu | Default seccomp and capabilities; no AppArmor profile; root daemon, no rootless mode |
| GPU | **Missing** | Device requests refused: the `real-gpu` part cannot run |
| Maturity | Very young | Public 13 days, six releases, one main author, 98 open issues |

The speed claim needs care. The 18 ms is a macOS figure. Kiri's own Ubuntu benchmark (in WSL 2) gives 188 ms for `docker run --rm` against Docker Engine's 697 ms. Published ports are about ten times slower, relayed in user space.

## What it would buy workbench

- **Faster container starts.** About half a second per container. `benchctl up` time is dominated by image builds, Slurm and database start and healthchecks, so the gain is small. The shared-stack test design (issue 234) already avoids most restarts.
- **Less engine memory.** No `dockerd`, `containerd` or per-container shim. `benchctl measure overhead` measures that cost on this machine (about 4 MB PSS per shim on the first live run, issue 456). The saving is likely in the low hundreds of MB, not GB. Not measured for Kiri.
- **One process to reason about.** Not a reason on its own.

None of these removes a current limit. The 16 GB budget is spent by Slurm nodes, Jira, Kubernetes and browsers, not by the engine.

## Risks and issues we would face

1. **Maturity.** Pre-1.0, public since 2026-11-02, one main author, a release every two days, issues filed faster than closed. Linux is the third platform after macOS and Windows. A test target needs a stable engine; an engine bug would look like a workbench bug.
2. **Priority classes break.** Every service sets `cgroup_parent: workbench-pN.slice`. Kiri ignores it, so the slices, the oomd drop-in on `workbench.slice` and per-class accounting stop working. The governor would have to group by label instead, or wait for upstream.
3. **Pause semantics.** Kiri's pause writes `cgroup.freeze` and waits for `frozen 1`, as runc does. The user-mode governor (ADR 0010) should work unchanged. The root governor's direct cgroup writes need the new path; whether Kiri tolerates writes behind its back is unknown.
4. **Swap is always off.** Kiri writes `memory.swap.max=0`. Today a container may swap up to its limit again. MariaDB, Jira and Prometheus would hit OOM sooner, and the governor's swap signals (ADR 0059) would read differently. Budgets may need re-measuring.
5. **Isolation proof for lab validation.** ADR 0081 says the throwaway slurmctld is "unreachable from and to the live stack". Docker enforces `--internal` with firewall rules; Kiri only removes the default route and keeps a gateway on the host bridge. Equivalence is unproved, and network create without a subnet fails on Linux (issue 228).
6. **Builds.** All four `images/` Dockerfiles and six component images are built locally; `slurm-ctl` and `login` build `FROM workbench/node-base`. Kiri builds in a BuildKit container, which fails on Linux today (issue 227). `docker load` as a workaround also fails (issue 85).
7. **Beside Docker.** Docker Engine sets the `FORWARD` policy to DROP ([Docker docs](https://docs.docker.com/engine/network/packet-filtering-firewalls/)). Kiri then has no outbound traffic (issue 247). A dual setup on one machine needs a workaround, and a spike must not run on the owner's machine with the cluster up.
8. **Security.** No AppArmor profile on Ubuntu, where Docker applies `docker-default`. The daemon is root and the `kiri` group is root-equivalent, as with Docker. Our watchdog and `doctor` checks read Docker's inspect fields; they exist in Kiri's API, but their meaning must be re-checked.
9. **GPU parts.** Kiri refuses device requests. The `real-gpu` part (ADR 0037) cannot run. The DCGM mock part needs no device and is unknown.
10. **Third-party images.** Jira DC with Postgres, MariaDB, Teleport, KWOK and the control plane, Prometheus, Grafana, Garage: none is known to run on Kiri. Large images may hit the 60-layer bug (issue 253).
11. **Install and no-sudo start.** Same shape as Docker: one `sudo` at install (`.deb`, then `kiri host-access`), no sudo after. Ubuntu 22.04 is not supported (issue 252). The app updates itself, which is bad for a pinned test target.
12. **CI.** No change: CI runs unit tests with `tests/hostbin/docker` and no engine. All the risk is in the local live tests (`verify.sh`).

## Options

| Option | What | Effort | Risk | Verdict |
|---|---|---|---|---|
| A. Full switch | Kiri replaces Docker Engine; SPEC P-2 rewritten | L to XL (weeks), blocked by issues 213 and 227 | High: loses slices, GPU part, AppArmor; an immature engine under every test | Reject now |
| B. Dual backend behind a small abstraction | Engine chosen by one setting; a resolver for cgroup paths and features; both engines supported and tested | M to L, plus double live-test time | Medium; permanent maintenance cost | Not now |
| C. Optional experimental backend | `--engine kiri`, off by default, known gaps listed, a subset of live tests | M (about one to two weeks) after a spike | Low for users; Docker stays the default | Only if the spike passes |
| D. Spike first, time-boxed | Two to three days on a separate machine or VM, a pass or fail list, results written into this ADR | S | Low | **Recommended** |

Because Kiri speaks the Docker API, the "abstraction" for B and C is thin: the socket (`DOCKER_HOST` or a Docker context, which `governor/dockerapi.py` already honours) plus one module that answers "where is this container's cgroup" and "does this engine support X".

### Effort by area, if C or A follows

Rough, for one agent with the owner running live tests.

| Area | Files | Change | Effort |
|---|---|---|---|
| Engine selection, `check`, `doctor`, `benchctl-launch` | `benchctl/machine.py`, `checks.py`, `scripts/benchctl-launch` | Detect and report the engine; Kiri's socket and group | S |
| cgroup path resolver | `governor/executors.py`, `benchctl/measure.py` | Kiri layout beside `docker-<id>.scope`; overhead process names | S to M |
| Priority classes without `cgroup_parent` | `benchctl/compose.py`, governor, `systemd/workbench.slice.d/` | Group by label, or wait for upstream; oomd backstop off on Kiri | M to L, blocked |
| Builds | `benchctl/stack.py`, `images/` | Wait for issue 227, or build with Docker and move images (needs a working `load`) | M, blocked |
| Lab validation isolation | `benchctl/lab_validate.py`, a live test | An explicit subnet; prove "unreachable both ways" with a test | S to M |
| GPU parts | `benchctl/real_gpu.py`, compose | Refuse `real-gpu` on Kiri with one clear message | S |
| Live tests | 43 files in `tests/real/` | Run all on Kiri, fix or mark differences | L |
| Docs | SPEC P-2, getting started, manual, guides (49 files mention Docker) | Engine choice, install, known gaps | M |

## Decision (proposed)

1. **Do not switch now** (option A rejected for the present). Kiri is two weeks old, its Linux mode has open blockers for builds and networking, and it ignores the `cgroup_parent` slices the governor's model rests on. The gain (seconds of start time, some hundreds of MB) does not remove any current limit.
2. **Run a time-boxed spike** (option D), only if the owner wants the option kept open. Two to three working days, on a separate Ubuntu 24.04 machine or VM, never beside the owner's running cluster. The spike changes no code on `dev`; its scripts and results go into this ADR's revision.
3. **Decide after the spike** between closing this ADR as "rejected for now" and building option C. Option B is not considered until Kiri reaches 1.0 or has had about three months of Linux releases with issues 213, 227, 228 and 247 closed.

### What the spike must prove

Each item is pass or fail, with the command and output kept.

1. Install the `.deb` beside Docker Engine; Docker keeps working; Kiri containers reach the network (issue 247); `apt purge` removes it cleanly.
2. `docker compose build` of `node-base`, then `slurm-ctl` and `login` built `FROM` it (issue 227).
3. `docker compose up` of the default profile on Kiri's socket: fixed IPs on both subnets, 127.0.0.1 ports, `service_healthy`, `tmpfs` sizes, `memory.max` per container, `SYS_PTRACE` on nodes only. `sinfo` shows nodes idle.
4. What happens to `cgroup_parent` (expected: ignored), and where each container's cgroup is.
5. The user-mode governor drives pause, unpause, kill, stop and start on Kiri's socket; the root governor's freeze, `memory.high` and `memory.reclaim` work on Kiri's cgroups.
6. An `--internal` network (with an explicit subnet) is unreachable both ways: from it to `10.141.0.0/16`, the bridge gateway, published ports and the internet, and back.
7. `inspect` gives what the watchdog and `doctor` read (`CapAdd`, `Privileged`, `SecurityOpt`, `Labels`, `State.Pid`, `State.Health`, `PortBindings`).
8. Third-party images start: Jira DC with Postgres, MariaDB, Teleport, KWOK and the control plane, Prometheus and Grafana.
9. Memory: Kiri's daemon and agent PSS against `dockerd`, `containerd` and shims for the same profile; the effect of swap off on MariaDB and Jira.
10. Time: cold `benchctl up` and `down`, and two `verify.sh --unit` runs, against Docker on the same machine.
11. `exec`, `cp` (including a named volume), `logs --since`, `stats --no-stream`, a `tcpdump` capture in a node, netem through `State.Pid`.
12. A Kiri daemon restart and a package upgrade with the stack up (issue 230).

**Pass bar for option C:** items 1 to 7 pass, or fail only in ways the backend can refuse with one clear message. Otherwise this ADR closes as rejected for now, with the revisit trigger in decision 3.

### Phases, if the owner later accepts option C

1. **Spike** (above), on its own issue.
2. **Experimental backend:** engine selection, the cgroup resolver, refusals for `real-gpu` and lab validation when isolation is unproved, docs as "experimental, Docker is the default". Docker stays the only engine in SPEC P-2.
3. **Parity:** slices and builds once upstream fixes land; nightly `verify.sh` on both engines for some weeks.
4. **Default switch:** only by a new owner decision and a SPEC change, never as part of phase 2 or 3.

### Rollback

At every phase Docker Engine stays installed and remains the default. The engine is one setting; the generated compose file does not change. Kiri keeps its state in `/var/lib/kiri` and `/var/lib/kiri-daemon`, apart from Docker's. Rolling back is "use the Docker socket again", then `apt purge kiri` if wanted.

## Consequences

- If accepted as proposed, nothing changes in code, SPEC or the install guide. `docs/progress.yaml` is not touched: nothing is built.
- A spike issue is opened only on the owner's word (see open questions).
- If the spike passes and C is built, every later Docker-specific feature needs a note on whether Kiri supports it.

## Open questions for the owner

1. Is this the Kiri you meant (`libracoder/kiri`, the Docker-daemon replacement)?
2. What is the goal: faster starts, less memory, or leaving Docker Engine itself? The answer decides whether a spike is worth it. If the goal is less engine memory or no root daemon, Podman is a longer-established engine with a rootless mode and a Docker-compatible API; it was not evaluated here. Should it be compared as well?
3. Is a separate Ubuntu 24.04 machine or VM available for the spike?
4. Would losing `cgroup_parent` slices and the oomd backstop be acceptable for an experimental backend?

## Not verified

Kiri was not installed or run; no workbench test ran on it; the owner's cluster was not touched. Every "yes" in the matrix comes from Kiri's documents or source at commit `193dfd7`, every "unknown" is for the spike. Memory and time gains are estimates, not measurements.
