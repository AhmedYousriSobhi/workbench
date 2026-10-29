# Getting started

How to run opti-sim on your workstation and see it working. Keep this page in step with the commands:
`tests/test_docs.py` fails if a `simctl` command named here no longer exists.

## 0. Install the tools (once)

Ubuntu has `python3`, not `python`, and `simctl` needs a few packages. Use a virtualenv in the repo:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
```

The venv is per shell: activate it again in every new terminal (`. .venv/bin/activate`), or skip activation with `./scripts/simctl <command>`, which uses `.venv` directly (`./scripts/simctl status`). With the venv active, `python -m simctl.cli ...` works as written below (or just `simctl ...`). The
governor service needs no packages; it runs on the system `python3`.

## 1. Check the machine

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

Open that link once (it sets a cookie; the token is kept in `compose/generated/secrets/ui_token`). **Governor** shows the
level banner, free memory and pressure over the last two minutes, and the governor's log. **Scenarios** lists the
fault scripts: pick a speed, press Play, watch each step turn ok/skipped/failed, press Stop to abort. Slurm steps are
skipped on this page (it has no Docker access by design); use `./scripts/simctl scenario run` for those.

## 5. When something is wrong

```bash
python -m simctl.cli status         # governor level and SECURITY findings
python -m simctl.cli diag           # one masked tarball: attach it to bug reports
```

If memory is tight the governor pauses then stops containers lowest priority first (P6 down to P2); that
is the design, see README section 6. `up` and `scale` are refused from level L2 up.

## 6. For developers

`./scripts/verify.sh` (add `--full` for the live stack) before asking for a merge; see CLAUDE.md.
