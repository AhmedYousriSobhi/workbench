# Plan: real entities instead of mocks, and a faster verify (issues 227 to 238)

Status: research done, nothing implemented. Written so each issue can be picked up cold.

**Budget (owner, 2026-11-02 session): 30 minutes in total for all sessions.** About 5 h of agent work is planned below, so
the 30 minutes covers only: 238 (this PR), then 235 (10 min) and the first step of 237 (per-step timing, 10 min). The rest waits
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
| 238 | ADR 0025 addendum | Written; add this research | 10 min | review |
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

---

## 227 node_exporter on every node

**Validated.** Upstream documents `--path.rootfs` plus a read-only root bind mount, but that is for monitoring the host from a
container; here each node monitors itself, so no host mount is needed. Collectors are switched off with
`--no-collector.<name>` or `--collector.disable-defaults` plus the wanted ones; the docs warn some collectors are high
cardinality or slow, so watch `scrape_duration_seconds`. [verified: node_exporter README via search summary] The upstream docs give no
memory figure, so it must be measured with `benchctl measure`.

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

The addendum is written (table of real candidates, cost, status, issue; and what cannot be real) and `validate.sh` passed on it.
To add from this research once the owner agrees: NHC license (BSD-3), the kind privilege conflict as the blocker for 232, the
mock NVML finding for 231, and the shape-comparison choice for 229. **Estimate: see the summary table (agent time), including commit, PR and progress file.**

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
