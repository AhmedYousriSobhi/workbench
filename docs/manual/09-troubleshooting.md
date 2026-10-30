# 9. Troubleshooting

Start with two commands:

```bash
./scripts/benchctl status          # governor level and security findings
./scripts/benchctl diag            # one masked tar.gz with host, Docker, libvirt and governor state
```

**Attach the `diag` file to any bug report.** Secrets in it are masked.

| Symptom | Likely cause | Fix |
|---|---|---|
| `check-machine.sh` says `NOT READY` | a missing prerequisite | follow the `fix:` line printed under it |
| `python: command not found` | Ubuntu has `python3` only | use `python3`, `./scripts/benchctl`, or activate `.venv` |
| `./scripts/benchctl` says to create a venv | `.venv` does not exist yet | `python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'` |
| `up` or `scale` refused | the governor is at L2 or above, or the profile does not fit | read the reason; free memory (close browsers, the IDE) and retry, or use a smaller profile |
| `up` names a smaller profile | not enough free memory for the one you asked for | run that profile, or free memory |
| First `start` takes minutes | images (Slurm) build from source | wait; later starts are fast |
| A shell on a node hangs | the container is paused by the governor | `benchctl status`; it thaws after 60 s calm |
| A node is gone | the governor stopped it, or you scaled down | `benchctl up --profile X` or `benchctl scale N` |
| Web page says the stack is down | the cluster is not running | `./scripts/benchctl start` |
| Web page says the governor is down | no governor socket | `./scripts/benchctl start` (no sudo) |
| Lost the login link | it is printed once at start | `./scripts/benchctl open` |
| Port already in use | another service holds 8088, 8080, 8081, 8000, 9400, 3000 or 9090 | stop it; set `BENCHCTL_UI_PORT` for the web page |
| Slurm steps skipped in a web scenario | the page has no Docker access by design | run `./scripts/benchctl scenario run NAME` |
| `ssh cn001` fails from the host | nodes are reachable only through the login node | `ssh` to the login node, then `ssh cn001`; or `docker exec -it workbench-cn001-1 bash` ([chapter 4](04-nodes-and-shells.md)) |
| `doctor` fails on privileged containers | a container was changed by hand | remove it and run `benchctl up` again |
| Everything is slow, then things stop | memory pressure; the governor is doing its job | `benchctl status`; free host memory |

## Upgrading from the old names

Earlier versions used `simctl`, `scripts/opti`, `OPTI_*` and, inside, the name `hpcsim` (Docker project, labels, the
governor socket, the systemd units). All are now `benchctl` and `workbench`. Once, after updating:

1. `./scripts/benchctl down -y` with the old checkout still running, or remove leftovers with `docker ps -a --filter label=hpcsim.class` and `docker rm -f` on them.
2. If you installed the root governor service: `sudo ./scripts/install-governor.sh` (it removes the old `hpcsim-governor` units and installs `workbench-governor`).
3. Rename in your own scripts: `simctl` and the `opti` script to `benchctl`, `OPTI_UI_PORT` and `OPTI_GOVERNOR` to `BENCHCTL_UI_PORT` and `BENCHCTL_GOVERNOR`, `HPCSIM_GRAFANA_TOKEN` to `WORKBENCH_GRAFANA_TOKEN`, `/run/hpcsim/gov.sock` to `/run/workbench/gov.sock`.
4. The Prometheus metrics start with `workbench_` now (`workbench_governor_level`, ...); update any dashboard of your own.

## Developers: tests that look like failures

If every module in `tests/real` errors at once in the `stack.up` fixtures, the governor probably refused `up` because host
memory is low. That is not a code failure and not a pass; check `./scripts/benchctl status` and free memory, then rerun.
Also keep throwaway test containers **unlabelled** (`workbench.class`), or the installed root governor freezes or stops them.

## Still stuck

Run `./scripts/benchctl diag`, then open an issue at the project's GitHub repository with the file attached and the exact
command you ran.
