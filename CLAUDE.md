# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

opti-sim: a memory-governed simulation of an HPC/AI cluster (Slurm, Kubernetes, BMC, BCM, Jira, Grafana) for a 16 GB Ubuntu workstation. Read [README.md](README.md) for architecture and rationale, and [SPEC.md](SPEC.md) for the requirements. When they disagree, SPEC.md wins; fix the other.

## Git workflow (mandatory)

- Before starting any new task or piece of work, create a GitHub issue for it (`gh issue create`: what, why, acceptance criteria). Branch from it, name the branch after it, and reference it in the PR (`Closes #N`).
- Never commit directly to `main` or `dev`. Always work on a separate branch, unless told to stick to a branch for a feature.
- Branch names: `feat/...`, `fix/...`, `docs/...`, `chore/...`, branched from `dev`.
- Open a PR to `dev` for every branch. `dev` collects all PRs; `dev` to `main` merges are made separately, only when asked.
- After each commit, add a PR comment describing that commit (what and why).
- Commit author must use the GitHub noreply address (the repo rejects private emails); this is set in local git config.
- Do not put tokens in files, commit messages, or output. The `origin` URL contains a token: mask it when printing remotes.

## Layout

See README section 9. Top level: `sim.yaml`, `profiles/`, `simctl/`, `governor/`, `systemd/`, `images/`, `components/`, `webui/`, `compose/`, `k8s/`, `fidelity-vm/`, `observability/`, `scenarios/`, `docs/adr/`.

## Conventions

- Python 3.11+, Typer for the CLI, FastAPI for services, type hints, `ruff` for lint/format, `pytest` for tests.
- Generated files (compose, `slurm.conf`, Prometheus targets) are derived from `sim.yaml`; never hand-edit them.
- Every component declares a memory budget and priority class (P0-P6).

## Hard rules

- The governor runs on the host, as root, and must not depend on anything it may stop. It must `mlockall` at startup and pre-allocate its mitigation path (SPEC GV-8).
- The Web UI never gets root or direct Docker/libvirt access; it only talks to `/run/hpcsim/gov.sock` (SPEC W-3).
- Do not add macOS-in-VM tooling (license). macOS is an external agent only.
- Do not add real NFS/Lustre or heavy JS frameworks; see SPEC C-11 and W-1.
- Record significant decisions as ADRs in `docs/adr/`.

- **Keep the progress tracker current.** Any PR that changes what is built updates `docs/progress.yaml` (status, how, where, PR; architecture nodes and edges), then `python -m simctl.progress` regenerates `docs/PROGRESS.md` and `docs/progress.html`. `validate.sh` fails on stale output. See [docs/guides/progress-and-diagrams.md](docs/guides/progress-and-diagrams.md).

## Commands

- `./scripts/validate.sh`: **run before every commit** (secrets scan, compile, ruff, bash -n, YAML/JSON, systemd verify, pytest, governor smoke). `./scripts/validate.sh --install-hook` makes it a pre-commit hook. Set `PYLIBS=<dir>` if tools are installed with `pip --target`.
- `./scripts/real-test.sh`: real-host tests (user cgroups via systemd-run, Docker, a live governor process). Run when touching `governor/`.
- `python -m governor --dry-run --once` (add `--watch-interval S` / `--no-watchdog` for the container security watchdog: new/resolved findings land in the audit log and `status`, skipped from L3 up); `--refresh-interval S` re-discovers containers (late starters get the current level's actions); `--grafana-url URL` publishes audit events as annotations (token only via `$HPCSIM_GRAFANA_TOKEN`, paused from L3 up); `--signals-file F.json` drives the ladder from a file for tests/tuning.

- `python -m simctl.cli doctor | preflight --profile X | status | diag`: environment check, memory pre-flight, governor state, and a masked diagnostics tarball. **Attach `diag` output when reporting or debugging an environment problem.**

- `python -m simctl.cli compose --profile X`: generates `compose/generated/docker-compose.yml` (gitignored) from `sim.yaml` + `profiles/X.yaml` (`groups`, `compute_nodes`, `extends`). Add components in `CATALOG` in `simctl/compose.py`; never hand-edit the output.
- `python -m simctl.cli measure run <label> [--duration S]`, then `measure report <idle> [<loaded>]`: Phase 0 read-only memory sampling (host, containers, PSS by process, VMs). Results go to `docs/measurements/` (gitignored); copy the suggested budgets into README section 5 and `profiles/*.yaml`.

- Containers must stay unprivileged (no `cap_add`, no `apparmor=unconfined`, no Docker socket) and memory-limited; `simctl doctor` fails otherwise. Slurm is built from source in `images/node-base` (pinned version + SHA256, ADR 0002).

- Generated secrets (DB password) live only in `compose/generated/secrets` (gitignored); never copy them into tracked files or output. REST access to Slurm: JWT via `scontrol token username=<user>` (not root), see ADR 0003.

- **`./scripts/verify.sh [--full | --only KEYWORD]`: run before asking for a merge** (validate.sh + real-host tests; `--full` adds the live stack lifecycle). Put reusable live-test helpers in `tests/real/helpers.py`, and add a `tests/real` test for every new live behaviour; assert on containers, not only on config.
- User guide: [docs/guides/getting-started.md](docs/guides/getting-started.md); update it when a user-facing command changes (`tests/test_docs.py` checks its commands). Standing governor service: `sudo ./scripts/install-governor.sh`.
- Governor `admit` (GV-9): `simctl up`/`scale` ask `/run/hpcsim/gov.sock` first (`$HPCSIM_GOV_SOCKET` overrides); governor `scale`/`set-profile` need `--workdir <repo>` and run only at L0-L1.
- Governor `--metrics-addr HOST:PORT` serves Prometheus `/metrics` (the install script binds the docker bridge gateway so the Prometheus container can scrape it; never 0.0.0.0). `tests/real/test_pressure_real.py` drives the ladder against the live stack.
- `./scripts/simctl <cmd>` runs simctl with `.venv` without activating it (the venv is per shell). Same as `python -m simctl.cli`.
- `python -m simctl.cli up --profile X | down | scale N`: pre-flight, ordered start of the compose project (`simctl/stack.py`), skips components without a Dockerfile, creates the governor's Grafana token once in `compose/generated/secrets/grafana_token`; `down` touches only the `hpcsim` project.

- `bcm-mock` (127.0.0.1:8081, CMDaemon-style `POST /json`) and `jira-mock` (127.0.0.1:8080, REST v2 subset, `POST /sim/failure`) are stdlib-only; surfaces and gaps in ADR 0005.

- `python -m simctl.cli scenario list | run NAME [--speed N] [--dry-run]`: scenario engine (`simctl/scenario.py`, ADR 0006) over `scenarios/*.yaml`; add a target in `execute()` and in `ENDPOINTS`/`validate`. gpu-exporter-farm publishes 127.0.0.1:9400 for injection.

- `python -m webui [port]`: Web UI on 127.0.0.1:8088 (ADR 0007), token in `compose/generated/secrets/ui_token`. Backend `webui/app.py` talks to the governor socket only and plays scenarios with `host=False` (no Docker). Keep it under 50 MB RSS (SPEC W-1; it sits near 48 MB now).

- **UI debugging:** `pip install -e '.[browser]' && .venv/bin/python -m playwright install chromium`, then `.venv/bin/python scripts/ui_shots.py [OUTDIR]` screenshots both tabs (desktop/phone, light/dark) and checks console errors, overflow and tab keyboard use; read the PNGs. `tests/real/test_webui_browser.py` runs the same checks. UI rules: status never by colour alone, text contrast >= 4.5:1 in both themes, tables stack as cards under 640 px, tap targets >= 40 px, ARIA tabs.

Not yet implemented; as each lands, document it here: `make demo`, `pytest`, `ruff check .`.
