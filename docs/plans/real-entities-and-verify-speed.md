# Plan: real entities instead of mocks, and a faster verify (issues 227 to 238)

Status: research done, nothing implemented. Written so each issue can be picked up cold.

## Start here (next session)

- **Tracking epics:** #254 real entities instead of mocks, #255 faster `verify.sh`, #256 multiple independent clusters and per-cluster
  identity. Each lists its issues in order as a task list; tracking only.
- **State at the end of the 2026-11-02 session:** research and planning only; nothing built, no behaviour changed. Open decisions:
  one compose project per cluster (recommended) or one project with prefixed names; the identity provider per cluster; the owner
  decisions listed per epic.
- **Suggested first picks, cheapest and most decisive:** #235 (10 min), #237 first step (per-step timing), then spikes #241 and #249.
- **Working rules for these issues:** one issue, one branch from `dev`, a PR with `Closes #N`, the definition of done in CLAUDE.md;
  the owner runs live tests and `verify.sh` (agent time excludes them, see the time model below); report a governor refusal as the
  environment (NF-6). Nothing here is to be built until the owner says so for that issue.
- **Memory figures:** only the Phase 0 baseline and the `validate.sh` timing are measured; every other number is an estimate tagged as
  such, and each issue's first step measures it.

**Budget (owner, 2026-11-02 session): 30 minutes in total for all sessions.** About 5 h of agent work is planned below, so
the 30 minutes covers only: 238 (PR 239, done), then 235 (10 min) and the first step of 237 (per-step timing, 10 min). The rest waits
for further sessions; nothing outside this list is started.

**Time model.** Estimates are agent working time in sessions capped at 30 minutes (reading, code, tests, docs, the PR). They
exclude two things that are not agent time: (a) live runs (`verify.sh`, `tests/real`, image builds), which the owner runs, about
5 to 15 minutes of wall clock each; (b) owner actions (a Jira Cloud account, a decision on privileges). An issue marked "2 sessions"
cannot honestly finish in 30 minutes; it is split so each session ends in a reviewable state. The earlier human-hour figures
(92 to 126 h) are withdrawn.

How to read the evidence tags: **[verified]** a primary source was read; **[reported]** a secondary or community source;
**[judgement]** my reasoning, to be confirmed by the first step of the issue.

## Summary

| Issue | Topic | Recommendation | Agent time | Needs from the owner |
|---|---|---|---|---|
| 238 | ADR 0025 addendum | Done in PR 239 (research findings included) | done | review of the PR |
| 235 | Bake pyyaml into the exporter | Do it; trivial | 10 min | 1 live run (exporter start time) |
| 233 | stress-ng jobs | Opt-in, total memory below the node limit | 25 min | 1 live run |
| 230 | Redfish validator | Run it, document the command, triage | 30 min (fixing findings may need a 2nd session) | none |
| 236 | Test-only Slurm/NHC timers | Opt-in, keep one default-timer test | 30 min | 2 live runs (flakiness check) |
| 237 | Fast/slow split, no double validate | Per-step timing, markers, stamp | 30 min | 1 live run |
| 234 | One shared stack for tests/real | Baseline first, then refactor | 2 sessions (5 + 30 min) | baseline run, then 2 live runs |
| 227 | node_exporter on nodes | Network, disk, load only (memory/CPU are host-wide) | 30 min | 1 live run, memory measurement |
| 229 | Real Jira responses | Shape comparison, not replay | 2 sessions (20 + 15 min) | a Jira Cloud free site and token (owner) |
| 228 | Upstream LBNL NHC | Spike and ADR; adoption separate | spike 20 min; adoption 2 sessions | decision after the spike |
| 231 | Real DCGM, fake GPUs | Spike only, time-boxed | 30 min | 1 live run (RSS, series) |
| 232 | Kubernetes | Privilege decision first, then build | ADR spike 20 min; build about 4 sessions | privilege decision (ADR) |
| | **Total** | | **about 5 h across 12 to 14 sessions** (about 2.5 h of that is 232 and the 228 adoption) | |

Within a single 30-minute session: 238, 235, 233, 230, 236, 237, 227, 231 and the two spikes (228, 232 ADR). These do not fit in
30 minutes and are split: 234, 229, the 228 adoption and the 232 build.

Suggested order: 238, then the verify group 235, 236, 234, 237 (about 1.5 h of agent time; they shorten every later issue's live
loop), then 230, 233, 227, 229, then the spikes 228 and 231, and 232 last.

A measurement already in hand: `validate.sh` on this branch ran in 119.9 s (725 passed, 118 skipped, coverage 96%).

## Targets and baseline (proposed; confirm after the first measurement)

Nothing here is measured for `verify.sh` yet; the only number in hand is `validate.sh` at about 100 to 120 s (101 s and 120 s on two
runs of the same tree). Targets are proposals for the owner to accept or change once issue 234's baseline exists.

| Metric | Baseline | Proposed target | How measured |
|---|---|---|---|
| Default `verify.sh` wall time | not measured | at least 40% lower than baseline | per-step seconds (issue 237) |
| Stack cold starts per default run | about 25 (one per module with a stack fixture) | 3 or fewer | `--setup-show` count (issue 234) |
| Longest single wait in the live tests | up to 300 s | 120 s or less with test timers | `--durations` (issues 234, 236) |
| Exporter start to first scrape | not measured, waits allowed up to 180 s | under 15 s | issue 235 |
| `validate.sh` | 101 to 120 s | unchanged (coverage floor 90% stays; 96% now) | existing output |

## Dependencies between issues

- 235 and 236 shorten waits that 234 would otherwise work around; do them first.
- 237's per-step timing is what makes 234's "before and after" credible; do its first step before 234.
- 227 (real metrics) and 233 (real job load) pair: a job's load only shows in real metrics if both exist.
- 231 must keep the Slurm-allocation-following behaviour of issue 83 and the label set of issue 64.
- 228 must keep the three NHC management paths (CLI, files, page, ADR 0019) and the 30 s live reload.
- 232 is blocked by the privilege decision; 227 and 231 add per-node memory that matters for 232's headroom (next section).

## Memory impact (measured baseline, estimated additions)

**Measured.** Phase 0 samples in `docs/measurements/` (taken 2026-10-29, under the project's former container-name prefix) show,
resident per container: a `slurmd` node 5.0 MB idle and 7.0 MB under load; a GPU node 5.1 to 5.6 MB; gpu-exporter-farm 18 MB;
bmc-farm 12 MB; Prometheus 21 to 22 MB; Grafana 57 to 59 MB; slurmctld 14 MB idle and 23 MB loaded; slurmrestd 7 MB; MariaDB 179 MB
with the accounting part. That sums to about 0.17 GB for `min`, matching the profile comment (budget 1 GB of limits).
**Host now (2026-11-02, cluster stopped):** 15.7 GB total, about 6.7 GB available, swap 1994 of 2047 MB used. Swap is nearly full
before any workbench container starts (the IDE and browser use several GB), so live runs of 227, 231 or 232 may be refused by the
governor (GV-9, NF-6); that would be the environment, not a defect.

**Estimated.** No number below for a new component has been measured; each is the first step of its issue (`benchctl measure`).
Source tag: [judgement] from typical figures for these programs, with a wide range, not a vendor number.

| Issue | Added per instance | Instances (min / std / full) | Added total (min / std / full) | Verdict |
|---|---|---|---|---|
| 227 node_exporter | 10 to 20 MB resident [judgement] | 6 / 18 / 34 nodes | 60 to 120 / 180 to 360 / 340 to 680 MB | Fits, but it roughly triples a node's 5 to 7 MB; the node limit is `SLURMD_MB` = 60, so check it |
| 231 real dcgm-exporter (mock NVML) | 50 to 300 MB [judgement, widest uncertainty] | 2 / 2 / 2 GPU nodes (default `sim.yaml`) | 100 to 600 MB in every profile | Versus 18 MB for all GPUs in the farm today; the spike must measure it, this is the main reason to expect "keep the farm" |
| 232 k3d or kind server | 500 to 1000 MB [judgement] | 1 / 1 / 1 | 0.5 to 1.0 GB | `std` and `full` already include "k3d + KWOK" in the architecture budget; not in `min` |
| 232 KWOK controller | 20 to 50 MB [judgement] | 1 | small; fake nodes cost API-server objects, not RAM per node | Fits |
| 233 stress-ng | at most `--vm-bytes` per running job | running jobs per node | bounded by the node limit (60 MB) minus slurmd, so keep `--vm-bytes` at or below about 25 MB | Memory is bounded; CPU is the real cost |
| 235 baked pyyaml | none steady; removes the transient `pip install` peak | 1 | slightly lower peak | Exporter limit stays 100 MB (18 MB steady) |
| 228, 229, 230, 234, 236, 237 | no resident process | | 0 (NHC runs for a moment per check; the validator and the Jira recorder run on the host in a throwaway environment) | None |

**Reading the table.** `min` has about 0.83 GB of headroom against its 1 GB budget at the measured 0.17 GB. 227 fits comfortably;
231 could consume most of that headroom on its own at the high end, which is the strongest argument for the spike to be
decision-focused (measure RSS first, stop if it exceeds the exporter-farm cost by an order of magnitude). 232 does not belong in
`min`. Update `profiles/*.yaml`, `docs/architecture/04-memory-and-profiles.md` section 5 and the README table with measured numbers
when each part lands (CLAUDE.md, measure run).

## Client features each issue could touch

| Issue | Client feature at risk | Check |
|---|---|---|
| 227 | Grafana panels reading node metrics | metric names and labels unchanged for existing series |
| 228 | Slurm node-issues view (drain reasons) | reasons keep the `NHC:` prefix, `sinfo -R` text unchanged |
| 229 | Jira search and ticket views | shapes only; no behaviour change |
| 230 | BMC over TLS, certificate pinning | the validator is read-only; a fix could change a field the client parses |
| 231 | GPU usage through `/api/ds/query` | labels `Hostname`, `gpu`, `UUID`, `device`, `modelName`; `FB_USED + FB_FREE` |
| 232 | Kubernetes view | new surface; the client-testing row changes from "not built" |
| 233 | Running and pending jobs panel | jobs still show as `sleep`-like in `squeue` |
| 234 to 237 | none (test harness only) | state "not exercised against the client" in each PR |

## Spec, progress and docs to update when each lands

C-6 (231), C-15 and ADR 0019 (228), C-2 (232), NF-2 evidence (229), C-3 and ADR 0012 (230), C-11 or C-7 (227); `docs/progress.yaml`
and the regenerated `docs/PROGRESS.md` and `docs/progress.html` for every issue that changes what is built; `client-testing.md`,
`getting-started.md` when a command changes, the wiki after the merge, and the CLAUDE.md lines that mention the exporter's pip
install (235), the timers (236) and `verify.sh` timings (237).

## Supply chain and security checklist

- Pin exact versions with SHA256 for each new binary (node_exporter, upstream NHC tarball, stress-ng package, validator), as ADR 0002
  does for Slurm; never `latest`.
- New listeners bind to 127.0.0.1 or the bridge, never 0.0.0.0; no new `cap_add`, no `apparmor=unconfined`, no Docker socket;
  `benchctl doctor` must still pass.
- NHC runs as root on nodes (Slurm `HealthCheckProgram`); keep the page's "never a script" rule (W-16).
- No tokens or site names in recorded fixtures (229); the privacy scan must pass.

## Flakiness policy (for 234 and 236)

- A change is stable after 3 consecutive passes of the touched modules alone and 2 of the full default run.
- A failure whose output says the governor refused `up` is the environment (NF-6); it is reported as such and is not a pass.
- A test that fails twice for timing is not retried silently; it gets a longer wait with a comment on why, or a marker, and an issue.

## Owner decisions in one place

1. 232: allow privileged kind or k3d node containers (an ADR exception), use rootless Docker for this part, or go KWOK-only.
2. 229: create a Jira Cloud free site and supply a token through an environment variable.
3. 228: adopt upstream NHC after the spike, or keep the stand-in.
4. 231: adopt real DCGM after the spike, or keep the farm.
5. The verify targets above.
6. GUI access (section "GUI access to every component"): accept or reject the optional `webterm` browser terminal; the default is
   copy commands, `ssh://` links and `benchctl ssh`, which add no privilege.

## Still to verify (not read in this research)

- kind's own rootless page and whether its node containers need `--privileged`.
- The official `kwokctl` docs for node templates, and whether KWOK can run a real API server without kind.
- The official DMTF validator README (certificate handling for a self-signed BMC).
- Whether `dcgm-exporter --fake-gpus` needs a driver, and the real RSS of exporter plus mock NVML.
- The `slurm.conf` text for `SlurmdTimeout` minimums.
- Settled: the only workflow is `.github/workflows/wiki.yml`; CI does not run `verify.sh` or `validate.sh`, so 237 only serves local runs and the hooks.

## Out of scope

Real NFS or Lustre (SPEC C-11), a macOS VM, real GPUs, replacing Slurm with a mock, running real training jobs, and any step that
needs sudo on the start, build or stop path (NF-5).

---

## 227 node_exporter on every node

**Validated.** Upstream documents `--path.rootfs` plus a read-only root bind mount, but that is for monitoring the host from a
container; here each node monitors itself, so no host mount is needed. Collectors are switched off with
`--no-collector.<name>` or `--collector.disable-defaults` plus the wanted ones; the docs warn some collectors are high
cardinality or slow, so watch `scrape_duration_seconds`. [reported: node_exporter README, seen through a search summary] The upstream docs give no
memory figure, so it must be measured with `benchctl measure` (see Memory impact below for the planning figure).

**Caveat that changes the scope [judgement, check in step 1].** Inside a container `/proc/meminfo` and `/proc/stat` show the
host's values, not the container's limit, so a stock `node_exporter` would report the same memory and CPU on every node. That is
real, but misleading as per-node data. A per-container view needs cgroup files (cAdvisor, or a textfile collector reading
`memory.current`), which is a different change.

**How I would do it**
1. Check what `node_exporter` reports in a node container for meminfo, netdev, netclass, filesystem, loadavg (30 min).
2. Add the binary in `images/node-base`'s single apt/layer (pinned version and SHA256, as for Slurm), run it with
   `--collector.disable-defaults` and only netdev, netclass, loadavg, filesystem (mount filter to skip overlay) and textfile.
3. Generate the Prometheus targets from `sim.yaml` (never by hand); add the part to `CATALOG` with a memory budget.
4. Keep the NIC-flap check working (the exporter farm still injects it; decide whether the real `node_exporter` carrier series
   replaces the synthetic one, which cannot be injected without `CAP_NET_ADMIN`; probably keep synthetic for the injected faults).
5. `tests/real` test on a live container's metrics; record the per-node memory in the profile table.

**Risk:** per-node RAM on the `full` profile (every node gets one). **Estimate: see the summary table (agent time).**

## 228 Upstream LBNL NHC instead of the stand-in

**Validated.** License is BSD-3 (LBNL text; Triad addition). [verified: LICENSE file] Needs Bash 4.3 or later; installs from the
release tarball with `./configure; make; make install` (not GitHub's auto-generated archive); the program goes to
`/usr/sbin/nhc`, config to `/etc/nhc`. Config lines are `target || check`, for example `* || check_hw_eth eth0`. It ships
`check_hw_ib`, `check_hw_eth`, `check_hw_physmem` and `check_nv_healthmon`, the last of which needs `nvidia-healthmon`.
[verified: README] Slurm side: `HealthCheckProgram` runs as root on nodes not in NOT_RESPONDING, the program is killed if it
does not finish within the timeout, and `HealthCheckTimeout` must be below `HealthCheckInterval`. [verified: slurm.conf page]

**Fit with this repo [judgement].** Upstream's built-in GPU and IB checks read hardware (`nvidia-healthmon`, `/sys`), which does
not exist here, so our XID, GPU-count, IB and NIC-flap checks stay custom scripts; what upstream would replace is the runner,
target matching, timeout handling and logging. The ADR 0019 file format (`checks.conf` plus one `NAME.sh`) differs from
`target || check`, and `benchctl/nhc.py` validates and syncs it with a 30-second live reload.

**How I would do it**
1. Spike (3 h): install upstream NHC in a scratch node, run one of our scripts as an NHC custom check, compare drain/resume
   behaviour and the prolog/epilog path.
2. Write the ADR: stand-in vs upstream vs wrapper (upstream runner, our `checks.conf` translated to NHC lines at sync time).
3. Only if adopted: implement the translation in `benchctl/nhc.py`, keep the `NHC:` reason prefix, keep validate-on-copy and
   the smoke run, and extend `tests/test_nhc*.py` and `tests/real/test_nhc_real.py`.

**Risk:** breaking the three management paths (CLI, files, page). **Estimate: see the summary table (agent time).**

## 229 Real Jira responses for the mock's contract tests

**Validated.** Jira Cloud has a free plan for development (about 10 users; log in at least monthly). Data Center needs a
time-limited evaluation or developer license and about 2 GiB or more of RAM; Data Center is being phased out and Cloud (v3) and
Data Center (v2) APIs have diverged. [reported: GitLab and Atlassian community pages] VCR.py-style recording must filter secrets
explicitly (`filter_headers`, `filter_query_parameters`, `filter_post_data_parameters`); redaction is not default; cassettes drift,
so re-record periodically and review them in PRs for leaks. [reported: pytest-recording and vcrpy docs via search]

**Design choice [judgement].** The thing under test is the mock server, not a client library, so replaying cassettes does not fit.
Record the real responses once to JSON, strip them down to shape (keys and value types, error codes and messages), and have a
contract test compare the mock's responses against those shapes. That also keeps personal data out of a public repo.

**How I would do it**
1. Create a Jira Cloud free site by hand (owner action; no token in files or output; the token only via an environment variable).
2. A recording script lists the endpoints the client uses (search/jql, search, myself, serverInfo, field, project, a refused
   transition) and writes sanitised shape files under `tests/fixtures/jira/` (no site name, account IDs, emails; the privacy scan
   must pass).
3. `tests/test_jira_contract.py` fails on shape drift; gaps go to ADR 0005.

**Risk:** the Cloud shape may differ from the Data Center shape the `dc` mode imitates; record both shapes if possible, or state
that only Cloud is covered. **Estimate: see the summary table (agent time).**.

## 230 DMTF Redfish-Service-Validator against bmc-farm

**Validated.** Installed from PyPI as `redfish-service-validator`, run as `rf_service_validator -r https://HOST -u USER -p PASS`
with `--authtype Basic|Session`; flags override a config file; it only issues GET requests and checks responses against the
Redfish schema. [reported: PyPI and a GitHub mirror; confirm against the official DMTF repository during step 1]

**How I would do it**
1. Install it into a throwaway venv (not the project venv) and run it against a per-node address (10.148.x.y:443) and against
   `127.0.0.1:8443` with the `Host: bmc-<node>` header (the validator takes a host, so the per-node address is the clean test;
   check how it handles the self-signed certificate).
2. Triage the findings into fix, known gap in ADR 0012 and `client-testing.md`, or false positive.
3. Add a documented copy-paste command; optionally a `tests/real` test that skips when the tool is missing.

**Risk:** the number of findings is unknown; fixing them is the open-ended part. **Estimate: see the summary table (agent time).**

## 231 Real DCGM with fake GPUs (spike only)

**Validated.** `dcgm-exporter` documents `--fake-gpus` (`DCGM_EXPORTER_USE_FAKE_GPUS`, default false, "accept fake GPU entities for
tests"); the page does not say whether it works with no GPU or driver. [verified: DCGM docs] NVIDIA's `k8s-test-infra` mock NVML
(Apache-2.0, v0.6.0, not v1) lets the real `dcgm-exporter` and `dcgmi` run with no GPU or driver, supports temperature, power,
XID and ECC, and DCGM profiling fields on Hopper and newer; limits: at most 8 GPUs, static device properties except optional
dynamic metrics, only 192 of 420 NVML functions implemented. [verified: pkg.go.dev page] The popular fake-gpu-operator emits only
utilisation and framebuffer series, so it is not enough. [reported: Grafana dashboard notes]

**Fit [judgement].** An H200 node here has 8 GPUs, which is exactly the mock's cap. But the farm is one process for all nodes,
while real DCGM would be one exporter per node, so memory per node is the cost to measure. Label parity (`Hostname`, `gpu`, `UUID`,
`modelName`, `device`) and scenario/Slurm-driven values must also be checked.

**How I would do it (spike, time-boxed to 6 to 8 h)**
1. Build or pull the mock NVML, run one `dcgm-exporter` against it in a scratch container; record RSS and image size.
2. Compare the series and labels with `components/gpu-exporter-farm`'s output and with what the client reads
   (`FB_USED + FB_FREE` equals the type's framebuffer).
3. Decide whether values can be driven by scenarios and Slurm allocations (the farm does both today).
4. Write the decision into ADR 0025. Default outcome: keep the farm unless the answers are all yes and the memory fits.

**Estimate: see the summary table (agent time).**

## 232 Kubernetes (real k3d/kind plus KWOK)

**Validated, and a blocker found.** kind supports rootless Docker since 0.11 (needs Docker 20.10 and cgroup v2, with restrictions
such as OverlayFS needing kernel 5.11 or later; this host runs 6.8). [reported: kind PR and commit; the kind rootless page itself
could not be fetched] Separately, a third-party page states kind uses privileged containers. [reported: Nestybox page] My own
knowledge agrees that kind and k3d node containers are privileged by default, which conflicts with the hard rule that containers
stay unprivileged and with `benchctl doctor`; treat this as **unconfirmed until step 1**. KWOK fake nodes are a Node object with
the annotation `kwok.x-k8s.io/node: fake`, `nvidia.com/gpu` under capacity and allocatable, and a NoSchedule taint; `kwokctl create
cluster --runtime kind` and `kwokctl scale node` are used by third-party GPU simulators. [reported: KWOK example page and a third-party
project; the official kwokctl docs were not reachable]

**How I would do it**
1. Decision spike (4 h): confirm what privileges a kind/k3d node needs on this host, measure RAM of a 1-server cluster, and check
   whether KWOK can run with a real API server without kind (its other runtimes), which might avoid privileged node containers.
2. ADR: either an explicit, documented exception (isolated network, no Docker socket, still memory-limited), rootless Docker for
   this part only, or a KWOK-only design. This needs the owner's decision.
3. Then implement as an optional part: `CATALOG` entry, memory budget, governor addressing by container label, scenario targets
   (node NotReady), `tests/real`, and the `client-testing.md` row.

**Risk:** the highest of the set (privilege rule, memory on 16 GB, a new part in every layer). **Estimate: see the summary table (agent time), after the decision.**

## 233 stress-ng for real small workloads

**Validated.** `--timeout` bounds a run; each `--vm` worker allocates the full `--vm-bytes`, so total memory is workers times
bytes; a container over its limit has the worker killed (signal 9), and the Docker limit excludes swap unless `--memory-swap`
is set. [reported: container examples; the rest is general knowledge, confirm in step 1]

**Fit [judgement].** `CgroupPlugin=disabled` (ADR 0002), so Slurm does not enforce memory; the container limit does, and an
overshoot would kill processes in the whole node container. So the job wrapper must cap total bytes well under the node limit.

**How I would do it**
1. Add `stress-ng` to node-base's apt layer; a wrapper script that computes `--vm-bytes` as a fraction of the node's limit and
   always passes `--timeout`.
2. Opt-in flag for `benchctl load` and `benchctl submit` (default stays `sleep`); document in the live-queue section.
3. `tests/real`: one job runs, ends, and the node's memory stays under the limit; one test with the governor ladder.

**Estimate: see the summary table (agent time).**

## 234 One shared stack for tests/real

**Validated.** Pytest Docker plugins converge on the same pattern: one session-scoped fixture that starts the stack, waits for
real readiness (a running container is not a ready service), and tears down once at the end; pin the project name and tear down a
leftover stack first; do not mix function-scoped starts of the same containers. [reported: avast/pytest-docker and
pytest-docker-compose READMEs] `--durations` and `--setup-show` give the baseline. [general knowledge]

**Fit [judgement].** 25 modules start and stop the stack today. Modules that change what runs (`test_apply_real`, `test_workbench_real`,
Teleport, the NHC check edits, SSH fault injection) cannot all share the default stack without restoring state.

**How I would do it**
1. Baseline (1 h): `WORKBENCH_REAL=1 pytest tests/real --durations=30 --setup-show`, saved without host paths. Do not start the
   rest if setup is not the dominant cost.
2. A session fixture for the default selection in `tests/real/conftest.py`; modules that only read use it and do not `down`.
3. Modules that change the selection keep their own fixture and run after the shared ones (or restore and `forget()`).
4. Each module must still pass alone (`verify.sh --unit NAME`).
5. Record before and after.

**Risk:** order dependence and leaked state. **Estimate: see the summary table (agent time).**

## 235 Bake pyyaml into the exporter image

**Validated.** Install dependencies at build time, not at container start; use `pip install --no-cache-dir`; pin exact versions;
a slim base; `PYTHONDONTWRITEBYTECODE` and `PIP_DISABLE_PIP_VERSION_CHECK`. [reported: Depot, WarpBuild, Datadog rule pages]
Today `compose/observability.yml` runs `pip install -q pyyaml` on every start, and `bcm-mock`/`bmc-farm` install it unpinned at build.

**How I would do it:** add a Dockerfile under `components/gpu-exporter-farm` with `pyyaml` pinned, build from compose (`benchctl up`
already passes `--build`), drop the `pip install` from the command, shorten `exporter_up` waits in the tests, and pin the same
version in the other two Dockerfiles. Measure start-to-serving before and after. Update the CLAUDE.md note about the pip install.
**Estimate: see the summary table (agent time).**

## 236 Test-only Slurm and NHC timers

**Validated.** `SlurmdTimeout` default is 300 s, may not exceed 65533, and 0 disables the check; the controller pings through the
hierarchical communication mechanism. `HealthCheckInterval` default 0 disables it; `HealthCheckTimeout` must be below the
interval (0 not allowed); the program is killed after the timeout (default 60 s). [verified: slurm.conf page] No documented
minimum for `SlurmdTimeout` was found; the current value is 15, so going lower risks false DOWN under load [judgement].

**How I would do it**
1. An opt-in environment variable (for example `WORKBENCH_TEST_TIMERS=1`) read by the `slurm.conf` generator and the NHC interval,
   never by hand-edited files; off by default so production defaults do not change. A changed `slurm.conf` already restarts
   `slurmctld` in `stack.up`.
2. Apply it in the slow live tests (`test_nhc_real`, `test_slurmresilience_real`, `test_apply_real`) and shrink their waits.
3. Keep one test at default timings (under `--full`), and keep `client-testing.md` stating about 30 s.

**Risk:** short timers causing flaky false DOWN; start with 10 to 15 s and measure. **Estimate: see the summary table (agent time).**

## 237 Fast/slow split and no double validate

**Validated.** Register custom markers and select with `-m "not slow"`; a common setup keeps the fast subset in the commit hook and
the full suite in pre-push or CI. [reported: pytest guides and a GitLab issue] I found no source on avoiding duplicate runs across
hook stages, so the skip rule below is [judgement] and needs a safe invalidation.

**How I would do it**
1. Print seconds per step in `verify.sh` PASS lines.
2. Markers `live`, `browser`, `slow` (registered in `pyproject.toml`), selectable with `--only`.
3. A stamp written by `validate.sh` on success, keyed by `HEAD` plus a hash of the working tree diff; `verify.sh` skips
   `validate.sh` only when the stamp matches, and says so on its output line. Never skip when untracked or staged files differ.
4. Coverage floor stays enforced at commit time; fix the stale header (`~1 min`, `~3 min`) with measured numbers.

**Estimate: see the summary table (agent time).**

## 238 ADR 0025 addendum

Done in PR 239: the addendum (table of real candidates, cost, status, issue; and what cannot be real) now carries the NHC license
(BSD-3), the kind privilege conflict as the blocker for 232, the mock NVML finding for 231, the host-wide memory caveat for 227 and
the shape-comparison choice for 229. **Estimate: see the summary table (agent time), including commit, PR and progress file.**

## Multiple clusters and the identity plane (issues 240 to 253)

Status: investigation only, nothing implemented. Evidence tags as above.

**Independence principle (owner, 2026-11-02).** Clusters are independent entities and usually unrelated to each other. They share no
accounting, no users, no keys and no network, and one never sees or affects another. An earlier draft of this section coupled them
(one `slurmdbd` for all, one login running `squeue -M`, one directory with group access); that is withdrawn. Coupling is allowed only
where the owner asks for it (a shared jump host, a federated pair), and is then an explicit option, never the default.

The design rule that follows: **share processes, never data.** The simulator can run one process for many clusters to save memory,
but each cluster gets its own endpoints, credentials and data, and a test proves one cannot read another (issue 251).

### Today

One cluster by design: `ClusterName=workbench`, one `slurmctld`, one network `10.141.0.0/16`, fixed service octets, one compose
project named `workbench`; the login nodes of ADR 0023 are several doors to the same cluster. The single-cluster assumption is in
about 40 files (`slurmctld`), 72 (`cn001`/`gpu001`), 17 (`10.141`) and 55 test files. The client profile's `clusters` list is one
entry per login route, and its `k8s` slot is always empty.

### Two planes

- **Cluster plane (per cluster, isolated):** Slurm or Kubernetes, its nodes, its login or head node, its own network and subnet, its
  users, its accounting if on, its keys.
- **Simulator plane (the workbench's own tooling, may serve every cluster from one process):** governor, scenario engine, the
  exporter and BMC farms, the mocks, Prometheus and Grafana. It reaches into each cluster the way a hypervisor does; clusters never
  reach into each other. [judgement]

### Names and projects [judgement, decide in issues 240 and 242]

Independent sites reuse names (every site has a `cn001`). One compose project per cluster (`workbench-alpha`) would allow that, make
`up` and `down` per cluster natural, and keep fault domains separate; the simulator plane would be its own project attached to
every cluster network. The cost: the code assumes one project named `workbench` (`compose.generate`, `stack.down`, the governor's
label discovery). The alternative, one project with cluster-prefixed names, keeps one project and one compose file (`depends_on` works everywhere), and the client-visible names can still repeat because the container `hostname` and per-network aliases are separate from the service name, but `up`, `down` and pause per cluster become service-list operations on one shared project, and one failed command touches every cluster. Recommendation: one project per cluster, because independence and cold clusters (issue 244) are the point; spike 241 confirms it. Subnets must
differ either way (Docker refuses two bridge networks with the same subnet), with no route between cluster networks.

### Jump hosts

By default each cluster has its own login or jump host on its own network, so the clusters stay independent. A shared bastion is an
opt-in that models one site gateway in front of several clusters: it is multi-homed on their networks and is the only thing that
touches more than one; nodes still cannot reach each other's networks. Slurm's `-M` multi-cluster mode depends on `slurmdbd`,
shared authentication keys and `ClusterName` registration [verified: slurm.schedmd.com/multi_cluster.html], so it would couple the
clusters; support it only as an explicit "federated" option if ever asked.

| Topology | Default | Memory | Tests |
|---|---|---|---|
| Separate jump or login per cluster | yes | one login container per cluster (about 10 MB RSS estimate plus about 12 MB shim) | isolation, wrong-jump-host errors |
| Shared bastion, opt-in | no | one more container, saves nothing | two-hop `ProxyJump`, a gateway outage hitting several clusters |
| Federated Slurm (`-M`), opt-in | no | shared `slurmdbd` | multi-cluster commands |

### sim.yaml shape (sketch)

```yaml
clusters:
  - name: alpha
    kind: slurm
    compute: {count: 4}
    gpu: {nodes: 2}
    users: [{name: alice}, {name: bob}]        # this cluster's users; another cluster has others
    identity: {provider: static}               # static (today) | ldap | ad
    access: {auth: ssh}
  - name: beta
    kind: slurm
    compute: {count: 8}
    users: [{name: carol}]
    identity: {provider: ldap}
    access: {auth: teleport}
  - {name: kube1, kind: k8s, real_agents: 1, kwok_nodes: 50, users: [{name: dave}]}
shared_access:                                  # optional: one gateway in front of several clusters
  - {name: gw, auth: ssh, reaches: [alpha, kube1]}
```

`login:` with no `clusters:` stays one cluster named `workbench`.

### Identity plane

Users belong to a cluster, so each cluster declares its own users and its own identity provider; nothing assumes the same person
exists in two clusters.

| Question | Finding | Evidence |
|---|---|---|
| Real AD protocol | Samba AD DC is the real AD-compatible server; OpenLDAP and 389-ds give LDAP only | [judgement] |
| One domain per cluster | a Samba AD DC instance is one domain, so an AD cluster costs one more instance; an LDAP server can hold separate base DNs per cluster in one process | [judgement], measure in issue 246 |
| Memory | no current figures found; one 2014 thread measured about 41 MB for Samba's parent process and suggested 64 MB; 389-ds autotunes from the container's cgroup limit when it can see it; FreeIPA is likely much larger | [reported, dated, no benchmark] |
| Node login | real `sssd` in a container needs an explicit `services` list (otherwise only the backend starts and `getent` fails); `getent passwd` is the check | [reported: a Docker fixture and image notes] |
| Per-node cost | real `sssd` only on a cluster's login or jump host, flat NSS files generated from that cluster's directory for its nodes (no per-node daemon) | [judgement], confirm in issue 246 |
| Teleport | Community Edition SSO is GitHub only; OIDC, SAML and Google are Enterprise; an AD-backed identity cannot federate into it, so Teleport users stay local | [reported: Teleport feature matrix and FAQ] |
| Teleport license | the editions page lists the Community license as commercial since version 16; another source says AGPLv3; sources disagree | [reported, unreconciled]; issue 249 |
| Keycloak in front | federates LDAP or AD and creates a local user record; Quarkus build uses less RAM than the old one (no figure) | [reported]; only if a client needs OIDC |
| Kubernetes OIDC | not researched | to verify in issue 246 |

Where a cluster's users matter: SSH on its login and nodes, its Slurm accounts (`sacctmgr` if accounting is on), Jira assignees,
Grafana, Teleport roles, Kubernetes RBAC, `user-sim` personas. Scenarios worth having: user offboarded with running jobs, locked
account, directory outage on one cluster while the others stay up.

### Ecosystem scope map

| Component | Plane and scope | Memory consequence |
|---|---|---|
| slurmctld, slurmrestd | cluster, per Slurm cluster | about 14 to 23 MB and 7 MB RSS measured; limits 200 and 60 MB |
| slurmd nodes | cluster | 5 to 7 MB RSS plus about 12 MB shim each (measured, one run) |
| slurmdbd and MariaDB | cluster, optional, off by default; if on, one MariaDB process with a database per cluster and one `slurmdbd` per cluster | MariaDB 179 MB measured once; `slurmdbd` about 5 MB each [judgement, spike 241] |
| login or jump host | cluster, one per cluster by default | about 10 MB RSS estimate plus shim |
| Teleport | **default: one shared instance** with per-cluster roles and node labels (owner, 2026-11-02); **on demand: one instance per cluster** for a test that needs a fully independent access system | 256 MB limit per instance, so each on-demand instance adds that; a shared instance links clusters through one access system, which the owner accepted as the default (issue 253) |
| Grafana | simulator plane, **one central instance (owner decision, 2026-11-02)**, one organization per cluster, each with its own viewer token and datasource | 58 MB measured once; a separate Grafana for a cluster only when a test needs it (issue 252) |
| Prometheus | cluster-scoped data: one small instance per cluster is the safe default (about 21 MB measured each) | one shared Prometheus would let one organization's datasource query another cluster's series, so sharing it needs label enforcement (an extra proxy process); decide in issue 252 |
| gpu-exporter-farm | simulator plane, one process, per-cluster series and endpoint | 18 MB measured once |
| bmc-farm and forwarders | farm in the simulator plane; forwarders per cluster network | consolidate (issue 247) |
| bcm-mock and the fake `cmsh` | simulator plane, one process, one head node per cluster as in real BCM | no new process |
| jira-mock | simulator plane, per-cluster project and credentials | no new process |
| Kubernetes | cluster | the largest item, 0.5 to 1 GB per real server (estimate); extra clusters KWOK-only (issue 248) |
| Directory and user management | cluster (per-cluster provider and base DN) | to measure (issue 246) |
| scenario-engine, user-sim, ticket-bot | simulator plane; targets take a cluster qualifier; personas per cluster | no new process |
| NHC checks | cluster, per-cluster file set | files only |
| Governor | host; acts per cluster by label, independent fault domains | admission by measured memory (issue 243) |
| Web page and export | cluster selector; one export entry per cluster (issue 245) | none |
| Storage and network chaos | per cluster network | none |

### Decision log

| Date | Decision | Source |
|---|---|---|
| 2026-11-02 | Clusters are independent entities, unrelated to each other | owner |
| 2026-11-02 | Grafana is centralised (one instance, one organization per cluster); a separate Grafana only when a test requires it | owner |
| 2026-11-02 | Every cluster has its own Prometheus (about 21 MB each); no shared Prometheus, so no label-enforcement proxy | owner |
| 2026-11-02 | Teleport: both modes supported. Default is one shared Teleport with per-cluster roles and labels; one Teleport per cluster is on demand, never the default | owner |

**Grafana organizations, validated.** An organization isolates dashboards, annotations, folders, data sources and service accounts;
users are the one thing shared across organizations; a service account acts only in the organization it was created in, so each
cluster needs its own service account and token. [reported: Grafana access-management and organization docs, via search] Datasource
uids need only be unique within an organization (a code comment in a charm patch; the docs mirror's provisioning sample is per
`orgId`), so every cluster can keep the client-facing uid `prometheus`; [reported, test on our version] provisioning takes a numeric
`orgId` that is not predictable, so organizations, tokens and datasources are created through the API at `up`, as the tokens are today
(`provision_token` in `benchctl/stack.py`). Grafana's own docs advise against organizations as the default way to group users at scale;
the cap here is about 8 clusters, so that does not bite. [reported]

**What this does not isolate.** An organization's datasource still points at a Prometheus. With one shared Prometheus, an
organization could query another cluster's series, so Prometheus stays per cluster (about 21 MB each), which the owner confirmed on 2026-11-02; label enforcement is not needed.
The cost saved by centralising is the 58 MB Grafana per extra cluster, not the Prometheus.

### Memory rules for the design [judgement, measured baseline above]

1. Share processes, never data: one process for the simulator-plane services, with per-cluster endpoints, credentials and data. Grafana is central, with an organization per cluster (owner decision).
2. Per Slurm cluster add only the controller, nodes and one login, about 55 to 70 MB of RSS plus shim.
3. Accounting off by default per cluster; when on, one MariaDB process with a database per cluster.
4. Declare many clusters, run few: independent lifecycle (cold clusters, issue 244), and the governor freezes one cluster without touching
   another.
5. At most one real Kubernetes control plane; others KWOK-only (issue 248, and the privilege question in 232).
6. Admission on measured memory, not on the sum of limits (issue 243).
7. Fewer containers: forwarders per cluster (issue 247).
8. Prove isolation (issue 251) whenever a process is shared.

### Issues and agent time

| Issue | Topic | Agent time |
|---|---|---|
| 240 | design and ADR: independent clusters, planes, project model, sim.yaml shape | 2 sessions |
| 241 | spike: two independent Slurm clusters (same node names, separate projects), shared MariaDB with a database each, measure shim, login, KWOK | 1 session plus a live run |
| 242 | per-cluster address plan; unique subnets, names unique per project | 2 sessions (after 240, 241) |
| 243 | governor and admission per cluster, independent fault domains | 2 sessions |
| 244 | cold clusters, `up --cluster` | 1 to 2 sessions |
| 245 | export with one entry per real cluster | 1 session |
| 246 | per-cluster identity (directory per cluster, user management) design and spike | 2 sessions plus a live run |
| 247 | BMC forwarders per cluster | 1 session (spike first) |
| 248 | extra Kubernetes clusters footprint | 1 session plus a live run |
| 249 | Teleport license and SSO limits | half a session |
| 250 | this section | done in PR 239 |
| 251 | isolation tests between clusters | 1 session plus a live run |
| 252 | central Grafana: organization, viewer token and datasource per cluster | 1 to 2 sessions plus a live run |
| 253 | Teleport shared by default, one per cluster on demand | 2 sessions plus a live run (after 240 and 249) |

Order: 241 and 249 first (cheap, they decide the rest), then 240, then 242 and 243, then 244, 245, 247, 251, with 246 and 248 in
parallel. Decided by the owner (see the decision log): clusters are independent, Grafana central with an organization per cluster,
Prometheus per cluster, Teleport shared by default and one per cluster on demand. Still open: one compose project per cluster or one
project with prefixed names (recommended: per cluster, pending spike 241), and the identity provider per cluster.

### Still to verify

Kubernetes API server OIDC settings; real RSS of the directory servers, `sssd`, a second `slurmctld`, a login node and the shim under
more containers; whether two clusters can share one MariaDB process with a database each under Slurm's `slurmdbd`; whether a Grafana
datasource with the same uid in two organizations works on our version (reported, not confirmed in the docs); the Teleport license text.

## GUI access to every component (proposed, issue number assigned on creation)

Status: step 1 (registry with link, copy and uri actions in Connections) implemented in issue 273; steps 2 to 5 not started. Evidence tags as above.

**Goal.** From the page, reach each component in one click: open a terminal on a node, open Grafana, Prometheus, Alertmanager,
the Teleport web UI, the Jira mock, or the owner's real Jira Cloud site (issue 229), and see the command for anything that is not a
web page. Today the Connections view (W-12) lists each part's address, a command to try and the credential path; this turns that
list into actions.

**Constraint that shapes the design.** The page never gets root, Docker or libvirt access and talks only to the governor socket
(W-3). So "open a terminal" cannot be `docker exec` from the page backend. [judgement, from SPEC W-3]

### Action kinds (one registry, like W-15)

Each component declares its actions in one registry (a sibling of `PART_INFO` in `benchctl/workbench.py`, served with
`/api/settings`), so a new component adds a registry entry and no page code.

| Kind | What the user gets | Fits |
|---|---|---|
| `link` | a button that opens an `http(s)` URL in a new tab (`rel="noopener noreferrer"`) | Grafana, Prometheus, Alertmanager, Teleport web UI, jira-mock, the web page's own help, Jira Cloud |
| `copy` | a button that copies a ready command (never a secret; the credential path stays a path) | `ssh`, `scontrol`, `sinfo`, `curl` Redfish, `tsh login` |
| `uri` | an `ssh://user@127.0.0.1:2222` link; the operating system opens it in whatever terminal the user registered for `ssh://` | login node, and a node through `ProxyJump` |
| `terminal` | an in-page terminal (optional part, below) | any node, for users who want no local setup |

### Open a terminal for a node: three levels, cheapest first

1. **Copy and URI (default, no new process).** For each node the page shows `ssh -J <user>@127.0.0.1:2222 <user>@cn001` (nodes
   sit behind the login node, ADR 0011 and 0018) and an `ssh://` link, using the key already generated under
   `compose/generated/secrets/ssh`. For a Teleport cluster it shows `tsh ssh <user>@<node>` instead (ADR 0020). It reuses existing
   access, so it adds no privilege. [judgement]
2. **`benchctl ssh [USER@]NODE`** (built, issue 275; the owner's wording replaced `open terminal`). `benchctl ssh cn001` is a root
   shell (docker exec), `benchctl ssh alice@cn001` a real SSH login through the login node. It runs on the host, as the user; the
   page shows it as a copy action and never runs it.
3. **Optional part `webterm` (off by default, `benchctl up --with webterm`).** One small container that serves a browser terminal
   and connects over SSH to the login node, so the shell is a normal SSH session and not a Docker exec. It binds 127.0.0.1 only,
   needs the page's token, is unprivileged and memory-limited, and holds a key valid only for the simulated users. Candidate
   programs (ttyd, wetty, or a small stdlib WebSocket relay with a vendored terminal widget) are compared in a spike for resident
   memory and licence before any is chosen; a heavy JS framework is excluded (W-1), and the widget must be vendored with no CDN.
   Estimate: tens of MB resident [judgement, measure with `benchctl measure`]. It adds a new SPEC component line and an ADR,
   because it is a new way in.

Not chosen: a governor `exec` command or a Docker-socket mount for the page. Either would let the page run commands in any
container and breaks W-3; the governor's allow-list stays closed (SPEC 4a).

### Jira Cloud (pairs with issue 229)

- The site URL and token are the owner's and are personal data in a public repo. The URL comes from a local setting
  (`$WORKBENCH_JIRA_CLOUD_URL` or a gitignored file), never from tracked files, an issue or a PR. The token stays in its
  environment variable and is never sent to the page.
- With `jira mode cloud` and the variable set, the Jira entry shows **Open Jira Cloud** (a `link`) next to **Open jira-mock**; with
  the variable unset it shows one line saying how to set it. It must not show a dead button (W-10).
- The link opens the site the user already has a session for; the page does not log in for them.

### Per-component actions (first cut)

| Component | Actions |
|---|---|
| Login node, each node | copy `benchctl ssh alice@NODE`, `ssh://` link, `webterm` when built |
| slurmctld, slurmrestd | copy `scontrol`, `sinfo`, JWT `curl` example (token command, not the token) |
| Grafana | link; copy the viewer-token path; after issue 252, one link per cluster organization |
| Prometheus, Alertmanager | link (per cluster after issue 252) |
| Teleport | link to the web UI (`teleport.workbench.test` or the 127.0.0.1 fallback), copy `tsh login` |
| bmc-farm | copy `curl` for a node's Redfish; link to `/redfish/v1` on 127.0.0.1:8443 (browser warns on the self-signed certificate; the page says so) |
| bcm-mock | copy `cmsh -c ...` through the login node, copy the `POST /json` example |
| jira-mock, Jira Cloud | link each; copy `curl` search example |
| Kubernetes (issue 232) | copy `kubectl --kubeconfig ...`; no web link |
| Governor | existing panel; copy `benchctl status` |

### Security and accessibility

- Links come only from the registry, which is built on the host; the page never builds a URL from user input. Allowed schemes:
  `http`, `https`, `ssh`. Nothing is opened automatically. No secret in a link, a button label or a copied command.
- Web links bind or point to 127.0.0.1 or the bridge, never 0.0.0.0 (checklist above). `webterm` also needs the CSRF header on
  its control endpoint and the page's token.
- Buttons follow the UI rules: at least 40 px tap target, status never by colour alone, a text label (not an icon alone), keyboard
  reachable, and the result of Copy announced to screen readers (`aria-live`).
- One message per condition: stack down shows one notice and hides actions that cannot work.

### How I would do it

1. Registry and `copy`/`link`/`uri` actions in the Connections view, for every component that already has an address
   (30 min, no new process, no live run beyond the page screenshots `scripts/ui_shots.py`).
2. `benchctl ssh [USER@]NODE` (done, issue 275, the owner's wording) and the Jira Cloud link with the unset-variable message (30 min; pairs with 229).
3. Spike for `webterm` (RSS, licence, vendored widget); ADR; build only if the owner accepts (about 2 sessions plus a live run).
4. Tests: a unit test that every registry entry has a valid scheme and no secret; a `tests/real` test that each `link` target
   answers and the `ssh` command in a `copy` action logs in; `ui_shots.py` for layout.
5. Update `docs/guides/client-testing.md` (where each endpoint is), getting-started, the manual's Connections section and its
   screenshots (`scripts/doc_shots.py`), SPEC W-12 and W-15, `docs/progress.yaml`, and the wiki after merge.

**Risk:** the `ssh://` handler is not registered on every desktop, so `copy` stays the dependable path; `webterm` is a new way in
and a new memory cost. **Client features at risk:** none, it adds page actions only; state "not exercised against the client"
unless the client's own SSH-hop profile is used to check the copied commands.

**Agent time:** steps 1 and 2 about 60 min (two sessions); step 3 about 2 sessions. **Needs from the owner:** accept or reject
`webterm`; the Jira Cloud URL kept locally.

## Sources

- node_exporter: https://github.com/prometheus/node_exporter
- NHC: https://github.com/mej/nhc and https://raw.githubusercontent.com/mej/nhc/master/LICENSE
- Slurm configuration: https://slurm.schedmd.com/slurm.conf.html
- VCR.py and pytest-recording: https://pypi.org/project/pytest-recording
- Redfish Service Validator: https://pypi.org/project/redfish-service-validator
- DCGM exporter flags: https://docs.nvidia.com/datacenter/dcgm/latest/reference/command-line-reference/dcgm-exporter.html
- Mock NVML: https://pkg.go.dev/github.com/NVIDIA/k8s-test-infra/pkg/gpu/mocknvml
- KWOK: https://kwok.sigs.k8s.io/ (inject-failure example)
- kind rootless support: https://github.com/kubernetes-sigs/kind/pull/1935
- pytest Docker fixtures: https://github.com/avast/pytest-docker and https://github.com/pytest-docker-compose/pytest-docker-compose
- Dockerfile practice: https://depot.dev/blog/dockerfile-linting-issues
- Slurm multi-cluster: https://slurm.schedmd.com/multi_cluster.html
- Teleport editions and feature matrix: https://goteleport.com/docs/choose-an-edition/introduction/
- Teleport license discussion: https://github.com/gravitational/teleport/discussions/39158
- Samba AD DC requirements thread (2014): https://lists.samba.org/archive/samba/2014-October/185988.html
