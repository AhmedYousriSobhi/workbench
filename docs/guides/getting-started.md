# Getting started

How to run opti-sim on your workstation and see it working. Keep this page in step with the commands:
`tests/test_docs.py` fails if a `simctl` command named here no longer exists.

## 0. Install the tools (once)

Ubuntu has `python3`, not `python`, and `simctl` needs a few packages. Use a virtualenv in the repo:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
```

After activating it, `python -m simctl.cli ...` works as written below (or just `simctl ...`). The
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
that does. Components not built yet (`bcm-mock`, `jira-mock`, `scenario-engine`) are skipped with a note.

## 4. What to look at

| What | Where |
|---|---|
| Grafana dashboards and governor annotations | http://127.0.0.1:3000 (user `admin`, password `admin`) |
| Prometheus | http://127.0.0.1:9090 (the `governor` job scrapes the governor's `/metrics`: `hpcsim_governor_level`, `hpcsim_host_mem_available_bytes`, ...) |
| Fake BMCs (Redfish) | `curl http://127.0.0.1:8000/cn001/redfish/v1/Systems/1` |
| Slurm jobs | `docker exec hpcsim-slurmctld-1 sinfo` / `squeue` / `sbatch` |
| Slurm REST | token: `docker exec hpcsim-slurmctld-1 scontrol token username=alice`; see ADR 0003 |
| Build progress and diagrams | `docs/progress.html` |

Inject a fault and watch it show up:

```bash
curl -X POST http://127.0.0.1:8000/sim/inject -d '{"node": "gpu001", "temp": 95, "fan_fail": 2}'
curl http://127.0.0.1:8000/gpu001/redfish/v1/Chassis/1/Thermal
curl -X POST http://127.0.0.1:8000/sim/inject -d '{"node": "gpu001", "clear": true}'
```

## 5. When something is wrong

```bash
python -m simctl.cli status         # governor level and SECURITY findings
python -m simctl.cli diag           # one masked tarball: attach it to bug reports
```

If memory is tight the governor pauses then stops containers lowest priority first (P6 down to P2); that
is the design, see README section 6. `up` and `scale` are refused from level L2 up.

## 6. For developers

`./scripts/verify.sh` (add `--full` for the live stack) before asking for a merge; see CLAUDE.md.
