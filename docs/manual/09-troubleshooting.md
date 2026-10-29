# 9. Troubleshooting

Start with two commands:

```bash
./scripts/simctl status          # governor level and security findings
./scripts/simctl diag            # one masked tar.gz with host, Docker, libvirt and governor state
```

**Attach the `diag` file to any bug report.** Secrets in it are masked.

| Symptom | Likely cause | Fix |
|---|---|---|
| `check-machine.sh` says `NOT READY` | a missing prerequisite | follow the `fix:` line printed under it |
| `python: command not found` | Ubuntu has `python3` only | use `python3`, `./scripts/simctl`, or activate `.venv` |
| `./scripts/simctl` says to create a venv | `.venv` does not exist yet | `python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'` |
| `up` or `scale` refused | the governor is at L2 or above, or the profile does not fit | read the reason; free memory (close browsers, the IDE) and retry, or use a smaller profile |
| `up` names a smaller profile | not enough free memory for the one you asked for | run that profile, or free memory |
| First `start` takes minutes | images (Slurm) build from source | wait; later starts are fast |
| A shell on a node hangs | the container is paused by the governor | `simctl status`; it thaws after 60 s calm |
| A node is gone | the governor stopped it, or you scaled down | `simctl up --profile X` or `simctl scale N` |
| Web page says the stack is down | the cluster is not running | `./scripts/opti start` |
| Web page says the governor is down | no governor socket | `./scripts/opti start` (no sudo) |
| Lost the login link | it is printed once at start | `./scripts/opti open` |
| Port already in use | another service holds 8088, 8080, 8081, 8000, 9400, 3000 or 9090 | stop it; set `OPTI_UI_PORT` for the web page |
| Slurm steps skipped in a web scenario | the page has no Docker access by design | run `./scripts/simctl scenario run NAME` |
| `ssh cn001` fails on your machine | nodes are reached only through the login node | `ssh -i compose/generated/secrets/ssh/id_ed25519 -p 2222 alice@127.0.0.1 ssh cn001` ([chapter 4](04-nodes-and-shells.md)); or `docker exec -it hpcsim-cn001-1 bash` |
| `doctor` fails on privileged containers | a container was changed by hand | remove it and run `simctl up` again |
| Everything is slow, then things stop | memory pressure; the governor is doing its job | `simctl status`; free host memory |

## Developers: tests that look like failures

If every module in `tests/real` errors at once in the `stack.up` fixtures, the governor probably refused `up` because host
memory is low. That is not a code failure and not a pass; check `./scripts/simctl status` and free memory, then rerun.
Also keep throwaway test containers **unlabelled** (`hpcsim.class`), or the installed root governor freezes or stops them.

## Still stuck

Run `./scripts/simctl diag`, then open an issue at the project's GitHub repository with the file attached and the exact
command you ran.
