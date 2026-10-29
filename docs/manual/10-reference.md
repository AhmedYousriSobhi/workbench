# 10. Reference

## Launcher: `./scripts/opti`

| Command | Does |
|---|---|
| `start [--profile P]` | machine check, venv, governor (as you), cluster, web page; default profile `min` |
| `stop` | stop the web page, the cluster and your own governor |
| `status` | governor, cluster and web page at a glance |
| `open` | print the login link, open a browser if there is a display |

## CLI: `./scripts/simctl` (same as `python -m simctl.cli`)

| Command | Does |
|---|---|
| `check` | machine check before building (OS, RAM, disk, Docker, ports); exit 1 if not ready |
| `doctor` | host prerequisites and container safety; exit 1 on any failure |
| `preflight --profile P` | would this profile fit in free memory now |
| `status [--socket S]` | governor level, signals, recent actions |
| `diag [--out F]` | masked diagnostics tarball |
| `compose --profile P` | generate `compose/generated/docker-compose.yml` |
| `up --profile P` | pre-flight, governor admission, ordered start (default profile `std`) |
| `down` | stop and remove the `hpcsim` project only |
| `scale N [--profile P]` | change the compute node count |
| `scenario list` | name, step count, description |
| `scenario run NAME [--speed N] [--dry-run]` | play a scenario; exit 1 on failure |
| `measure run LABEL [--duration S] [--interval S]` | sample memory to `docs/measurements/` |
| `measure report IDLE [LOADED]` | markdown table with suggested budgets |

Run `./scripts/simctl COMMAND --help` for the exact flags.

## Scripts

| Script | Does |
|---|---|
| `scripts/check-machine.sh` | stdlib-only machine check, before any build |
| `scripts/install-governor.sh [--uninstall\|--print]` | optional root governor service (sudo) |
| `scripts/install-hooks.sh` | git hooks for contributors (no sudo) |
| `scripts/validate.sh` | lint, tests, privacy scan: run before every commit |
| `scripts/verify.sh [--full]` | validate plus real-host tests: run before asking for a merge |
| `scripts/real-test.sh` | real-host tests |
| `scripts/ui_shots.py [OUTDIR]` | screenshots and checks of the web page |
| `scripts/check-privacy.sh` | scan for personal data before publishing |

## Other entry points

| Command | Does |
|---|---|
| `.venv/bin/python -m webui [port]` | the web page (default 8088) |
| `python -m governor [flags]` | the governor (`--dry-run`, `--once`, `--user-mode`, `--socket`, `--metrics-addr`, `--grafana-url`, `--thresholds`, `--workdir`, `--refresh-interval`, `--no-watchdog`) |
| `fidelity-vm/bin/up.sh`, `down.sh` | the on-demand VM |

## Ports (127.0.0.1, except 9401)

| Port | What |
|---|---|
| 8088 | web page |
| 8080 | jira-mock |
| 8081 | bcm-mock |
| 8000 | bmc-farm (Redfish, `/sim/inject`) |
| 9400 | gpu-exporter-farm |
| 3000 | Grafana (admin / admin) |
| 9090 | Prometheus |
| 9401 | governor `/metrics` (on the docker bridge address, so Prometheus can scrape it) |

## Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `OPTI_UI_PORT` | `opti` | web page port (default 8088) |
| `OPTI_GOVERNOR` | `opti` | `auto`, `user`, `system` or `off` |
| `HPCSIM_GOV_SOCKET` | `simctl`, web page | governor socket path override |
| `HPCSIM_GRAFANA_TOKEN` | governor | token for annotations (set by `opti`; never put it in files) |
| `COV_FLOOR`, `PYLIBS` | `validate.sh` | coverage floor, tool directory |

## Files and directories

| Path | What |
|---|---|
| `sim.yaml`, `profiles/` | topology and profiles |
| `scenarios/` | fault scripts |
| `compose/generated/` | generated compose file, secrets, `ui.pid`, `ui.log`, `governor.log` (not committed) |
| `docs/measurements/` | memory measurements (not committed) |
| `docs/adr/` | decision records |
| `/run/hpcsim/gov.sock` | root governor socket; the rootless one is under `$XDG_RUNTIME_DIR/hpcsim/` |

## Container names

`hpcsim-<service>-1`: `slurmctld`, `slurmrestd`, `mariadb`, `slurmdbd`, `bmc-farm`, `bcm-mock`, `jira-mock`,
`scenario-engine`, and one per node (`cn001`, `gpu001`, ...).

## Glossary

| Term | Meaning |
|---|---|
| Profile | a named set of components with a memory budget |
| Governor | the host process that protects memory |
| PSI | Linux pressure stall information; the governor's main signal |
| Level L0-L5 | the governor's escalation ladder |
| Class P0-P6 | container priority; the highest number is acted on first |
| Scenario | a named, timed list of faults |
| Mock | a program that imitates one service's API |
| Fidelity VM | the one real VM, on demand |
