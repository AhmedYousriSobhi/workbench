# Research note: Kiri as a Docker Engine alternative

Supporting note for [ADR 0085](../adr/0085-kiri-container-engine-plan.md) (Proposed); issue 800. Research only: no code was changed and Kiri was not installed or run on any machine. Every Kiri fact below was read from its repository at commit [`193dfd7`](https://github.com/libracoder/kiri/tree/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba) (2026-11-15, release v0.2.1) or its issue tracker on 2026-11-15.

Short links used in the tables: `K:` is a file in that Kiri commit, `KI #N` a Kiri issue, `WB:` a workbench file.

## 1. Which Kiri

The only lead was a Show HN summary ([zeli.app/story/50353261](https://zeli.app/story/50353261)): "Kiri, an open-source Rust container runtime with 18 ms Docker runs". The HN item ([news.ycombinator.com/item?id=50353261](https://news.ycombinator.com/item?id=50353261)) was flagged; its four comments are about the post being written by an AI, not about the software.

The submitter, `libracoder`, owns [github.com/libracoder/kiri](https://github.com/libracoder/kiri): Rust, Apache-2.0, homepage runkiri.com, "A small, fast container runtime for macOS, Windows and Linux". Same author, same claim, same language: this is the project the post describes.

Other GitHub projects named Kiri are not container engines: a screenshot tool (`yuxino/Kiri`), a local-ports viewer (`GaoSSR/Kiri`), a desktop shell (`tau-OS/kiri-desktop-old`), a key remapper. No name clash for a container engine was found.

**What Kiri is, in one line:** not a new CLI or a runc replacement, but a replacement for the Docker *daemon*. It serves the Docker Engine API on its own socket, and the real `docker` CLI, Compose and buildx talk to it ([K: README.md](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/README.md)).

## 2. Project facts

| Fact | Value | Source |
|---|---|---|
| Licence | Apache-2.0; the guest kernel is GPL-2.0; the name and logo are reserved; contributions need a CLA | [K: README.md](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/README.md), [K: CLA.md](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/CLA.md) |
| Public since | 2026-11-02 (first public commit "Kiri 0.1.14"; earlier history not public) | GitHub API, commits |
| Releases | v0.1.14 (11-02), v0.1.15, v0.1.17, v0.1.18, v0.2.0 (11-10), v0.2.1 (11-15): six in 13 days, all pre-1.0 | [releases](https://github.com/libracoder/kiri/releases) |
| People | One main author (82 of about 98 public commits); 10 others with 1 to 4 commits each | GitHub API, contributors |
| Activity | 5,411 stars, 251 forks, 98 open issues, 48 closed, 186 PRs (2026-11-15) | GitHub API |
| Focus | A Docker Desktop and OrbStack replacement; macOS first (VM, menu bar, Finder), then Windows (WSL 2) and Linux | [K: docs/features.md](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/docs/features.md) |
| Docker API version served | 1.47 | [K: crates/kiri/src/daemon/http.rs L10](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri/src/daemon/http.rs#L10) |
| Updates | The app updates itself after a signature check; crash reports are opt-in | [K: SECURITY.md](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/SECURITY.md), [K: docs/releases/0.1.17.md](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/docs/releases/0.1.17.md) |

### How it runs on Linux

On Linux there is no VM: the `kiri` daemon and its agent (`kiri-init`) run on the host kernel, as root, from a systemd unit with `Delegate=yes` ([K: contrib/linux/kiri.service](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/contrib/linux/kiri.service)). The agent starts each container with one `clone3` into namespaces and a cgroup. There is no runc, no containerd, no shim.

Install is a `.deb` or `.rpm`, then `sudo kiri host-access $USER`, which adds the user to a `kiri` group. Kiri itself says membership is root-equivalent, like the `docker` group ([K: packaging/README.md L44-L52](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/packaging/README.md#L44-L52)). The socket is `/run/kiri/docker.sock`, so it can sit beside Docker Engine.

No rootless mode is documented or present in the source (no match for "rootless" in `crates/`). cgroup v2 is required ([K: kiri-init linux/mod.rs L319](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri-init/src/linux/mod.rs#L319)).

### The performance claims

The HN title says 18 ms. The README says 2.55 ms through the API and 14.2 ms through the `docker` CLI, measured on a Mac against Docker Desktop and OrbStack ([K: README.md](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/README.md)).

Kiri's own Ubuntu benchmark is the relevant one ([K: bench/ubuntu-2026-10-26.md](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/bench/ubuntu-2026-10-26.md)). It ran on Ubuntu 24.04 inside WSL 2, not bare metal:

| Measure (median) | Kiri | Docker Engine |
|---|---|---|
| `docker run --rm alpine true` | 188 ms | 697 ms |
| `docker exec … true` | 187 ms | 224 ms |
| Published port throughput (iperf3) | 10.3 Gbit/s | 100.7 Gbit/s |
| `docker build`, 3 layers, no cache | 1,113 ms | 1,723 ms |

So the start is about 3.7 times faster on Linux, not 18 ms. Published ports are about ten times slower because Kiri relays them in user space ([L49-L50](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/bench/ubuntu-2026-10-26.md#L49-L50)). Host-to-container stdin over 4 MiB is dropped (same file).

Kiri says its daemon stays at about 15 MB. That is a macOS figure for the idle daemon; no Linux memory figure for a running engine with 20 or more containers is published.

## 3. What workbench needs from Docker

Counted on `dev` at `f8103b7` with `git grep` for `docker` invocations, `"docker"` argument lists, `docker.sock` and `DOCKER_HOST`. A line count is a lower bound: helpers called from many places count once.

| Area | Files | Lines | What it does with Docker |
|---|---|---|---|
| `benchctl/` | 29 | 138 | `docker compose -p workbench` up, down, build (`stack.py`); `exec`, `cp`, `logs`, `stats`, `inspect`, `pause`, `stop`, `network create --internal`, `run --rm` (scenarios, workload, lab validation, Slurm upgrade, capture, measure, Jira, Kubernetes) |
| `governor/` | 6 | 29 | Engine API on the socket for `pause`, `unpause`, `kill`, `stop`, `start` (`dockerapi.py`); `ps` and `inspect` for discovery; cgroup files under `docker-<id>.scope` (root mode); `State.Pid` plus `nsenter` for netem; the security watchdog reads `HostConfig` |
| `webui/` | 2 | 2 | None directly (SPEC W-3); two references only |
| `scripts/` | 2 | 2 | `benchctl-launch`, `verify.d/lab.sh` |
| `images/`, `components/` | 4 + 6 Dockerfiles | | Multi-stage builds; `slurm-ctl` and `login` are built `FROM workbench/node-base` (a local image) |
| `tests/real/` | 43 | 129 | Live tests assert on containers through the CLI |
| Unit tests | 34 | 111 | Stubbed by `tests/hostbin/docker`; no engine needed |
| Docs | 49 | | Install, getting started, manual, guides, ADRs |

Compose keys the generator emits ([WB: benchctl/compose.py](../../benchctl/compose.py)): `mem_limit`, `cgroup_parent` (`workbench-p0.slice` to `workbench-p6.slice`), `cap_add: [SYS_PTRACE]` on Slurm nodes, `ipam` subnets with fixed `ipv4_address`, ports on 127.0.0.1, `tmpfs` with a size, `healthcheck` with `depends_on: condition: service_healthy`, `devices` (CDI, real GPU only), labels.

Cgroup layout assumptions: the root governor and `benchctl measure` look for `/sys/fs/cgroup/<slice>/docker-<id>.scope` ([WB: governor/executors.py L38-L41](../../governor/executors.py), [WB: benchctl/measure.py](../../benchctl/measure.py)). SPEC P-2 names "Docker with the systemd cgroup driver".

## 4. Capability and gap matrix

Status: **yes** (documented or in the source), **partial**, **no**, **unknown** (not documented and not settled by reading the source; the spike must test it). "Source" means the code was read, not run.

### Engine, lifecycle and API

| Need | Used by | Kiri | Evidence |
|---|---|---|---|
| Docker Engine API on a Unix socket | governor, CLI | yes, API 1.47 at `/run/kiri/docker.sock` | [http.rs L10](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri/src/daemon/http.rs#L10), [kiri.service](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/contrib/linux/kiri.service) |
| Real `docker` CLI and Compose plugin | everything | yes (Kiri ships pinned copies or uses the installed ones) | [K: packaging/README.md](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/packaging/README.md) |
| `pause` / `unpause` (cgroup v2 freeze) | governor L3 | yes, writes `cgroup.freeze` and waits for `frozen 1` | [cgroup.rs L26-L37](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri-init/src/linux/cgroup.rs#L26-L37) |
| `stop -t`, `kill`, `start`, `restart`, `rm` | governor, scenarios | yes | [features.md L10-L14](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/docs/features.md#L10-L14) |
| `exec` (`-u`, `-d`, `-i`), `cp`, `logs --since/--tail`, `stats --no-stream`, `top` | benchctl, tests | yes; `cp` from a named volume fails (KI #245) | [features.md](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/docs/features.md), [KI #245](https://github.com/libracoder/kiri/issues/245) |
| Labels and `ps --filter label=` | governor discovery | yes ("filters on every list"); image `Config.Labels` missing in inspect (KI #206) | [features.md L14](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/docs/features.md#L14), [KI #206](https://github.com/libracoder/kiri/issues/206) |
| `inspect` fields: `HostConfig.CapAdd`, `Privileged`, `SecurityOpt`, `State.Pid`, `State.Health` | watchdog, doctor, netem | yes (fields present in the source); `HostConfig.PortBindings` is null (KI #293) | [api.rs](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri/src/daemon/api.rs), [KI #293](https://github.com/libracoder/kiri/issues/293) |
| `GET /events` | not used today | yes | [ops.rs L27](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri/src/daemon/ops.rs#L27) |
| Healthchecks, `depends_on: service_healthy` | MariaDB, Jira's Postgres | partial: healthchecks run as Docker runs them; the Compose condition is client-side, unknown until tried | [health.rs](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri/src/daemon/health.rs) |
| Restart policies | stack | yes, `always`, `unless-stopped` come back after a daemon restart | [docs/releases/0.2.1.md](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/docs/releases/0.2.1.md) |
| Daemon restart keeps containers sane | upgrades | partial: overlay mounts leak across a daemon restart on Linux (KI #230); a package upgrade restarts the engine and stops containers | [KI #230](https://github.com/libracoder/kiri/issues/230), [packaging/README.md L75](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/packaging/README.md#L75) |

### Cgroups and memory

| Need | Used by | Kiri | Evidence |
|---|---|---|---|
| cgroup v2 | everything | yes, required | [mod.rs L319](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri-init/src/linux/mod.rs#L319) |
| `mem_limit` as `memory.max` | every service | yes | [cgroup.rs L13-L24](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri-init/src/linux/cgroup.rs#L13-L24) |
| Swap per container | memory model | **differs**: Kiri always writes `memory.swap.max=0`; Docker's default lets a container swap | [cgroup.rs L18](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri-init/src/linux/cgroup.rs#L18) |
| `cgroup_parent` (P0-P6 slices) | governor classes, oomd backstop | **no**: `CgroupParent` is never read and is silently ignored | no match in `crates/`; [KI #213](https://github.com/libracoder/kiri/issues/213) |
| systemd cgroup driver, `docker-<id>.scope` | SPEC P-2, root governor, measure | **no**: containers live in `<kiri.service cgroup>/kiri/<id>` | [mod.rs L233-L293](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri-init/src/linux/mod.rs#L233-L293) |
| Writable `memory.high`, `memory.reclaim`, `cgroup.kill` for the root governor | governor root mode | unknown (plain cgroup v2 files, so likely writable by root; untested) | as above |
| `docker update` of limits | not used today | yes | [features.md L10-L14](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/docs/features.md#L10-L14) |
| `tmpfs` with a size | MariaDB, Prometheus | yes (`--tmpfs`); a tmpfs loses uid, gid and mode in one case (KI #296) | [KI #296](https://github.com/libracoder/kiri/issues/296) |

### Networking

| Need | Used by | Kiri | Evidence |
|---|---|---|---|
| User networks with `ipam` subnet, fixed `ipv4_address` | `10.141.0.0/16`, `10.148.0.0/16` | yes (`--subnet`, `--gateway`, `--ip`) | [features.md L21-L39](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/docs/features.md#L21-L39) |
| Network without a subnet | lab validation's throwaway net | **fails on Linux**: "no free subnet left" (KI #228) | [KI #228](https://github.com/libracoder/kiri/issues/228) |
| Embedded DNS by service name | every service | yes; aliases resolve across projects on a shared network (KI #294) | [KI #294](https://github.com/libracoder/kiri/issues/294) |
| Ports on `127.0.0.1` | page, mocks, Jira, Grafana | yes, relayed in user space (about 10 times less throughput) | [bench L49-L50](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/bench/ubuntu-2026-10-26.md#L49-L50) |
| `--internal`, isolated both ways | ADR 0081 lab validation | **partial**: the container gets no default route; no firewall isolation rule was found; the host-side bridge keeps a gateway address | [net.rs L653-L693](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri-init/src/linux/net.rs#L653-L693) |
| Outbound traffic on a host with `FORWARD` policy DROP | image builds, pulls inside containers | **fails**: no accept or masquerade rules (KI #247). Docker Engine itself sets that DROP policy, so Kiri beside Docker hits it | [KI #247](https://github.com/libracoder/kiri/issues/247) |
| Netem: `nsenter -t <State.Pid> -n tc` | governor root mode | unknown (host PIDs expected on Linux; untested) | |

### Images and builds

| Need | Used by | Kiri | Evidence |
|---|---|---|---|
| Pull from Docker Hub, `registry.k8s.io`, `nvcr.io`, `public.ecr.aws` | node-base, KWOK, Teleport, DCGM | yes (`docker login` credentials) | [features.md L41-L44](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/docs/features.md#L41-L44) |
| `docker compose build`, multi-stage Dockerfiles | `benchctl up` | partial: BuildKit in a privileged container Kiri manages; on Linux every build fails today (KI #227) | [builder.rs L1-L13](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri/src/daemon/builder.rs#L1-L13), [KI #227](https://github.com/libracoder/kiri/issues/227) |
| `FROM workbench/node-base` (a local image) | `slurm-ctl`, `login` | unknown: a BuildKit container normally resolves `FROM` from a registry, not the engine's store | |
| `docker load` | fallback for builds | **fails** (400, KI #85, partly fixed) | [KI #85](https://github.com/libracoder/kiri/issues/85) |
| Images with many layers | Jira DC | unknown: 60 or more layers fail to start (KI #253) | [KI #253](https://github.com/libracoder/kiri/issues/253) |

### Security and devices

| Need | Used by | Kiri | Evidence |
|---|---|---|---|
| Default capabilities, `cap_add: SYS_PTRACE` | Slurm nodes (ADR 0074) | yes | [features.md L15-L20](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/docs/features.md#L15-L20) |
| Default seccomp filter | all | yes (Kiri's own); custom profiles limited (KI #130) | [KI #130](https://github.com/libracoder/kiri/issues/130) |
| AppArmor `docker-default` on Ubuntu | all | **no**: no AppArmor support in the source | no match in `crates/` |
| User namespace remap, `--cgroup-parent`, `enable_icc=false` | not used except `cgroup_parent` | no, accepted and ignored (KI #213) | [KI #213](https://github.com/libracoder/kiri/issues/213) |
| `--gpus`, `deploy.devices`, CDI `devices` | `real-gpu` part (ADR 0037) | **no**: device requests are refused ("Kiri does not support GPUs") | [hostopts.rs L61-L71](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/crates/kiri/src/daemon/hostopts.rs#L61-L71) |
| DCGM mock part (no device) | `dcgm` part (ADR 0025) | unknown (an ordinary unprivileged image; untested) | |
| Engine socket for a non-root user, no sudo at start | ADR 0010, SPEC | partial: one `sudo` at install, like Docker; then the `kiri` group | [packaging/README.md L44-L52](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/packaging/README.md#L44-L52) |
| Ubuntu 24.04 package | install | yes; Ubuntu 22.04 fails (needs glibc 2.39, KI #252) | [KI #252](https://github.com/libracoder/kiri/issues/252) |
| systemd integration | install, restart | yes, `kiri.service` with `Delegate=yes` | [kiri.service](https://github.com/libracoder/kiri/blob/193dfd7e18d5bd3e01e9aae1a52b9e87565524ba/contrib/linux/kiri.service) |

### Third-party images workbench runs

None of these is tested against Kiri anywhere we could find. All are ordinary Linux images, so they should start if the features above work; each is **unknown** until the spike runs it.

| Image | Part | Specific risk on Kiri |
|---|---|---|
| `atlassian/jira-software:10.3.9` + `postgres:15` | `jira-real` (ADR 0026) | layer count (KI #253), `service_healthy`, 2.1 GB limit without swap |
| `mariadb:11` | Slurm accounting | `tmpfs` data dir with a size, healthcheck script |
| `public.ecr.aws/gravitational/teleport-distroless` | Teleport | none known |
| `registry.k8s.io/kwok/kwok`, `etcd`, control-plane images | Kubernetes (ADR 0034) | fixed IPs, many ports |
| `prom/prometheus`, Grafana, Alertmanager, `dxflrs/garage` | observability, object store | `tmpfs` size for the TSDB |
| `nvcr.io/nvidia/k8s/dcgm-exporter` | `dcgm` part, node-base build stage | pull from `nvcr.io` |

## 5. Open Kiri issues that touch workbench

Listed from [the tracker](https://github.com/libracoder/kiri/issues) on 2026-11-15 (98 open). Most were filed in the last two days, which shows interest, not stability.

| Issue | Effect on workbench |
|---|---|
| [#227](https://github.com/libracoder/kiri/issues/227) every `docker build` fails on Linux | `benchctl up` cannot build the images |
| [#228](https://github.com/libracoder/kiri/issues/228) network create without a subnet fails on Linux | lab validation's throwaway network |
| [#247](https://github.com/libracoder/kiri/issues/247) no outbound traffic with `FORWARD` DROP | Kiri beside Docker Engine, builds that fetch packages |
| [#213](https://github.com/libracoder/kiri/issues/213) `CgroupParent` ignored | P0-P6 slices, oomd backstop, root governor |
| [#230](https://github.com/libracoder/kiri/issues/230) overlay mounts leak across a daemon restart | nothing starts until unmounted by hand |
| [#294](https://github.com/libracoder/kiri/issues/294) DNS answers from the first-joined network | multi-cluster plans with shared networks |
| [#293](https://github.com/libracoder/kiri/issues/293) `PortBindings` null in inspect | `doctor` and export read ports |
| [#253](https://github.com/libracoder/kiri/issues/253) 60 or more layers fail | large vendor images |
| [#245](https://github.com/libracoder/kiri/issues/245) `cp` from a named volume fails | shared-storage tests |
| [#252](https://github.com/libracoder/kiri/issues/252) `.deb` needs glibc 2.39 | Ubuntu 22.04 hosts |

## 6. What this note did not do

- Kiri was not installed, run or benchmarked. Every "yes" is from documents or source reading.
- No workbench test ran against Kiri. The owner's running cluster was not touched.
- No independent review of Kiri's security was done; its own `SECURITY.md` was read.
- Alternatives with a Docker-compatible API on Linux (Podman, nerdctl with containerd) appear in Kiri's own benchmark table but were not evaluated here.
