# Getting started

How to run opti-sim on your workstation and see it working. Keep this page in step with the commands:
`tests/test_docs.py` fails if a `simctl` command named here no longer exists.

## Quickest way

```bash
./scripts/opti start      # machine check, tools, cluster, web page; prints the login link
./scripts/opti status     # what is running
./scripts/opti stop       # stop the web page and the cluster
```

The numbered sections below are the same steps one at a time, for when you want to see or change each.

## 0. Check the machine (before building anything)

```bash
./scripts/check-machine.sh
```

Needs only the system `python3`. It checks the OS, CPU cores, RAM, free disk, cgroup v2, Docker (daemon and
compose), free ports, optional tools (KVM, k3d, kubectl) and says which profiles fit in the memory available
right now. It ends with `READY` or `NOT READY` and a fix line for each problem. Do not build until it says
`READY`. The same check runs as `python -m simctl.cli check` once the venv exists.

## 0b. Install the tools (once)

Ubuntu has `python3`, not `python`, and `simctl` needs a few packages. Use a virtualenv in the repo:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
```

The venv is per shell: activate it again in every new terminal (`. .venv/bin/activate`), or skip activation with `./scripts/simctl <command>`, which uses `.venv` directly (`./scripts/simctl status`). With the venv active, `python -m simctl.cli ...` works as written below (or just `simctl ...`). The
governor service needs no packages; it runs on the system `python3`.

## 1. Check the setup (doctor)

```bash
python -m simctl.cli doctor        # cgroup v2, PSI, Docker, swap, memlock, containers stay unprivileged
python -m simctl.cli preflight --profile min    # would this profile fit in free memory right now?
```

`WARN kubectl/k3d missing` is fine until Kubernetes profiles exist. Fix any `FAIL` first.

## 2. Start the governor (outside monitoring and control)

The governor watches memory and security and may stop low-priority containers under pressure. Run it
before the stack.

```bash
sudo ./scripts/install-governor.sh        # standing service, starts at boot
python -m simctl.cli status               # level, memory, groups, security findings
```

For a one-off foreground run instead:
`sudo python -m governor --socket /run/hpcsim/gov.sock --workdir $PWD --grafana-url http://127.0.0.1:3000`.
Remove the service with `sudo ./scripts/install-governor.sh --uninstall`.

## 3. Start, scale and stop the cluster

```bash
python -m simctl.cli up --profile min     # pre-flight, governor admission, ordered start (~1 min)
python -m simctl.cli scale 2              # compute nodes (slurm.conf regenerated, slurmd started/stopped)
python -m simctl.cli down                 # stops only the hpcsim project; your other containers are untouched
```

Profiles are in `profiles/` (`min`, `std`, `full`); `up` refuses a profile that does not fit and names one
that does. Components not built yet are skipped with a note.

## 4. What to look at

| What | Where |
|---|---|
| Grafana dashboards and governor annotations | http://127.0.0.1:3000 (user `admin`, password `admin`) |
| Prometheus | http://127.0.0.1:9090 (the `governor` job scrapes the governor's `/metrics`: `hpcsim_governor_level`, `hpcsim_host_mem_available_bytes`, ...) |
| Fake BMCs (Redfish) | `curl http://127.0.0.1:8000/cn001/redfish/v1/Systems/1` |
| Fake BCM (CMDaemon JSON) | `curl -X POST http://127.0.0.1:8081/json -d '{"service": "cmdevice", "call": "getDevices"}'` |
| Fake Jira | `curl -X POST http://127.0.0.1:8080/rest/api/2/issue -d '{"fields": {"project": {"key": "OPS"}, "summary": "hi"}}'`; failures: `curl -X POST http://127.0.0.1:8080/sim/failure -d '{"mode": "429", "count": 3}'` |
| Slurm jobs | `docker exec hpcsim-slurmctld-1 sinfo` / `squeue` / `sbatch` |
| Slurm REST | token: `docker exec hpcsim-slurmctld-1 scontrol token username=alice`; see ADR 0003 |
| Build progress and diagrams | `docs/progress.html` |

Inject a fault and watch it show up:

```bash
curl -X POST http://127.0.0.1:8000/sim/inject -d '{"node": "gpu001", "temp": 95, "fan_fail": 2}'
curl http://127.0.0.1:8000/gpu001/redfish/v1/Chassis/1/Thermal
curl -X POST http://127.0.0.1:8000/sim/inject -d '{"node": "gpu001", "clear": true}'
```

Or run a named scenario (a timed list of faults) instead of injecting by hand:

```bash
./scripts/simctl scenario list
./scripts/simctl scenario run node-down --dry-run   # show the timeline, change nothing
./scripts/simctl scenario run node-down --speed 10  # play it ten times faster
```

A scenario fails (exit 1) when a step errors or an `expect` line does not hold. Steps for parts that are not
built yet (Kubernetes, ticket-bot) are reported as skipped.

## 4b. The web page

```bash
.venv/bin/python -m webui        # prints a login link such as http://127.0.0.1:8088/login?token=...
```

Open that link once (it sets a cookie; the token is kept in `compose/generated/secrets/ui_token`). Everything is on one page. The strip at the top shows the governor level, free memory, how many services and nodes are healthy, any running scenario, and a theme switch (auto, light, dark). The **left panel** is the governor: level, memory and pressure with two-minute trends. The **middle** is the cluster: service chips, the traffic diagram (a round dot travels from *Clients* to a service for each real request; a diamond from *Fault injector* when a fault is applied; *Pause animation* stops them, and they stop by themselves at governor L3 or for reduced motion) and a card per node. Open *Fault actions* on a node to power it off, overheat it, fail its fans or inject a GPU error, then *Clear faults*. The **right panel** has *Needs attention*, the scenarios (pick a speed, press Play, watch each step turn ok/skipped/failed, press Stop to abort) and the governor's log. Slurm steps are skipped on this page (it has no Docker access by design); use `./scripts/simctl scenario run` for those. On a phone the panels stack under each other.

## 5. When something is wrong

```bash
python -m simctl.cli status         # governor level and SECURITY findings
python -m simctl.cli diag           # one masked tarball: attach it to bug reports
```

If memory is tight the governor pauses then stops containers lowest priority first (P6 down to P2); that
is the design, see [the governor](../architecture/05-governor.md). `up` and `scale` are refused from level L2 up.

## 6. For developers

To look at the web page without a screen: `pip install -e '.[browser]'`, `.venv/bin/python -m playwright install chromium`, then `.venv/bin/python scripts/ui_shots.py` writes screenshots to `/tmp/opti-ui-shots` and reports problems.

`./scripts/verify.sh` (add `--full` for the live stack) before asking for a merge; see CLAUDE.md.
