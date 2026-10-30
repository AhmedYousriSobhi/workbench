# 10. Reference

## The command: `benchctl`

One command, one grammar, like `kubectl`: **`benchctl VERB [RESOURCE] [NAME] [flags]`**. Run it as `./scripts/benchctl`
(no activation needed), or as `benchctl` once the venv is active. Tab completion for verbs, resources, node and service names, profiles, scenarios, users, parts, `-o` formats and `-l`/`--sort-by` keys:
`benchctl completion install` (once, no sudo, then open a new terminal; `benchctl start` offers it too; `completion remove` undoes it; `completion bash|zsh|fish` prints the script). A wrong profile, node or scenario gets one message with `Did you mean` and the valid values. What each verb does not do, exit codes, and what to use in a script: [the verbs guide](../guides/benchctl-verbs.md).

| Verb | Does |
|---|---|
| `start [--profile P | --with PART ...]` | machine check, venv, governor (as you), cluster, web page; default profile `min` |
| `stop` | stop the web page, the cluster and your own governor |
| `open` | print the login link, open a browser if there is a display |
| `get nodes\|services\|profiles\|scenarios\|workbench [-o json\|name] [-l SEL] [--sort-by COL] [-w]` | list what exists, filter (`-l kind=gpu`), sort, redraw; `nodes` shows every node `sim.yaml` defines and its state (`absent` = not started) |
| `describe node NAME [-o json]` | everything known about one node: container, Slurm, jobs, GPUs busy/idle, BMC |
| `exec NODE [-u USER] [-- CMD]` | shell on a node, or one command: `benchctl exec gpu001 -- nvidia-smi` |
| `logs NODE [-f] [--tail N]` | container log of a node or service |
| `plan [selection]` | estimate the memory a workbench needs against free memory; exit 1 if it does not fit ([guide](../guides/choose-workbench.md)) |
| `up --profile P` | pre-flight, governor admission, ordered start (default profile `std`) |
| `down [-n] [-y]` | stop and remove the `workbench` project only; asks first in a terminal, `-n` only lists |
| `scale N [--profile P] [-n] [-y]` | change the compute node count; asks before removing nodes in a terminal, `-n` only says what would change |
| `up --cpu-nodes N --gpu-nodes N [--gpus-per-node N] [--with PART] [--without PART] [-f FILE]` | the same by selection: estimate first, build only if it fits |
| `apply -f FILE` | build the workbench a file describes |
| `scenario list` / `scenario run NAME [--speed N] [--dry-run]` | list scenarios; play one, exit 1 on failure |
| `export profile [-o FILE] [--bmc] [--user U] [--name N] [--format ID] [--configured]` | write the running cluster as a client profile file (also the page's **⇩ Export** button) ([guide](../guides/export-profile.md)) |
| `ssh-key install\|remove` | put the login key under `~/.ssh` for clients that only read there |
| `status [--socket S]` | governor level, signals, recent actions, then whether the cluster and the web page run |
| `check` | machine check before building (OS, RAM, disk, Docker, ports); exit 1 if not ready |
| `doctor` | host prerequisites and container safety; exit 1 on any failure |
| `preflight --profile P` | would this profile fit in free memory now |
| `diag [--out F]` | masked diagnostics tarball |
| `compose --profile P` | generate `compose/generated/docker-compose.yml` |
| `benchctl measure run LABEL [--duration S] [--interval S]` | sample memory to `docs/measurements/` |
| `benchctl measure report IDLE [LOADED]` | markdown table with suggested budgets |

`benchctl COMMAND --help` shows the exact flags. All the planned verbs of ADR 0015 now exist.

## Scripts

| Script | Does |
|---|---|
| `scripts/sync_wiki.py --out DIR \| --push` | generate the GitHub Wiki from `docs/`, or sync it to the wiki repository ([how](../guides/github-wiki.md)) |
| `scripts/check-machine.sh` | stdlib-only machine check, before any build |
| `scripts/install-governor.sh [--uninstall\|--print]` | optional root governor service (sudo) |
| `scripts/install-hooks.sh` | git hooks for contributors (no sudo) |
| `scripts/validate.sh` | lint, tests, privacy scan: run before every commit |
| `scripts/verify.sh [--full]` | validate plus real-host tests: run before asking for a merge |
| `scripts/real-test.sh` | real-host tests |
| `scripts/ui_shots.py [OUTDIR]` | screenshots and checks of the web page |
| `scripts/check-privacy.sh` | scan for personal data before publishing |
| `scripts/check-identity.sh` | refuse a wrong or placeholder git identity before a commit or push |

## Other entry points

| Command | Does |
|---|---|
| `.venv/bin/python -m webui [port]` | the web page (default 8088) |
| `http://127.0.0.1:8088/help/` | these pages inside the web page, with search (needs the login) |
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
| `BENCHCTL_UI_PORT` | `benchctl start` | web page port (default 8088) |
| `BENCHCTL_GOVERNOR` | `benchctl start` | `auto`, `user`, `system` or `off` |
| `WORKBENCH_GOV_SOCKET` | `benchctl`, web page | governor socket path override |
| `WORKBENCH_GRAFANA_TOKEN` | governor | token for annotations (set by `benchctl start`; never put it in files) |
| `COV_FLOOR`, `PYLIBS` | `validate.sh` | coverage floor, tool directory |

## Files and directories

| Path | What |
|---|---|
| `sim.yaml`, `profiles/` | topology and profiles |
| `scenarios/` | fault scripts |
| `compose/generated/` | generated compose file, secrets, `ui.pid`, `ui.log`, `governor.log` (not committed) |
| `docs/measurements/` | memory measurements (not committed) |
| `docs/adr/` | decision records |
| `/run/workbench/gov.sock` | root governor socket; the rootless one is under `$XDG_RUNTIME_DIR/workbench/` |

## Container names

`workbench-<service>-1`: `slurmctld`, `slurmrestd`, `mariadb`, `slurmdbd`, `bmc-farm`, `bcm-mock`, `jira-mock`,
`scenario-engine`, and one per node (`cn001`, `gpu001`, ...).

## Glossary (short; the full list is [chapter 12](12-glossary.md))

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
