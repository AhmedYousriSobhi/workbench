# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

workbench: a memory-governed simulation of an HPC/AI cluster (Slurm, Kubernetes, BMC, BCM, Jira, Grafana) for a 16 GB Ubuntu workstation. Read [README.md](README.md) for the overview, [docs/architecture/](docs/architecture/README.md) for the architecture and rationale, and [SPEC.md](SPEC.md) for the requirements. When they disagree, SPEC.md wins; fix the other.

**Main purpose:** workbench is the test target for a desktop HPC monitoring client (SPEC G5). To test one against it, read [docs/guides/client-testing.md](docs/guides/client-testing.md) first. Never name that client in this repo.

## Git workflow (mandatory)

- Before starting any new task or piece of work, create a GitHub issue for it (`gh issue create`: what, why, acceptance criteria). Branch from it, name the branch after it, and reference it in the PR (`Closes #N`).
- Never commit directly to `main` or `dev`. Always work on a separate branch, unless told to stick to a branch for a feature.
- Branch names: `feat/...`, `fix/...`, `docs/...`, `chore/...`, branched from `dev`.
- Open a PR to `dev` for every branch. `dev` collects all PRs; `dev` to `main` merges are made separately, only when asked.
- After each commit, add a PR comment describing that commit (what and why).
- **Definition of done for every feature (mandatory, put these as to-dos in the issue before starting):**
  1. Update the related docs (manual, guides, ADR, SPEC, `docs/progress.yaml`; getting-started if a command changed).
  2. The wiki is published by CI after the merge to `dev` (`.github/workflows/wiki.yml`); check the run and the in-page help. By hand: `python scripts/sync_wiki.py --push` ([docs/guides/github-wiki.md](docs/guides/github-wiki.md)). Check the in-page help shows the change.
  3. Check it is easy to follow: a new user can do it from the docs alone, commands copy-paste, one message per condition.
  4. Test it against the monitoring client ([docs/guides/client-testing.md](docs/guides/client-testing.md)) and state what was and was not exercised.
- Commit author must use the repository owner's GitHub noreply address (`<id>+<login>@users.noreply.github.com`); GitHub credits a commit to the account with that **ID**, so a placeholder such as `1+x@...` credits a stranger, permanently (merged PRs keep it; fixing it cost a history rewrite and a new repository). **Before the first commit of a session run `./scripts/check-identity.sh`** (and `git config user.name user.email`); a name like `x`, an unfamiliar ID or an identity you did not set yourself is a stop sign: fix it, do not commit. The pre-commit and pre-push hooks and `validate.sh` run the same check; never bypass it.
- Do not put tokens in files, commit messages, or output. The `origin` URL contains a token: mask it when printing remotes.

## Public repository: privacy (mandatory)

This repo is public: whatever is committed, and any text in an issue, PR or comment, is world-readable and may be cached.
- Never publish personal data: home-directory paths (`/home/<you>/...`, including scratchpad and tool-output paths copied from a terminal), the local username, personal emails, hostnames, tokens, private keys. Use placeholders (`/home/user`, `you@example.com`) and repo-relative paths, in issue, PR and comment text too.
- Local tooling stays local and is gitignored: `.claude/` (its skills are symlinks into a home directory; committing one publishes the path) and `graphify-out/` (generated; rebuild with `/graphify`). Never `git add -f` them, and add files by name, not `git add -A` or `.`.
- Run `./scripts/install-hooks.sh` once per clone (no sudo). It points git at the tracked `.githooks/`: **pre-commit** (refuses commits on `main`/`dev`, runs `scripts/check-privacy.sh --staged`, then `validate.sh`), **commit-msg** (needs an issue reference such as `(#12)`, scans the message), **pre-push** (refuses pushes to `main`/`dev`, runs `check-privacy.sh --all`). `validate.sh` runs the privacy scan too. Never bypass with `--no-verify` or `ALLOW_PROTECTED_BRANCH`; fix the cause.
- `scripts/check-privacy.sh` prints `file:line [rule]` and never the matched text. Private terms only you know (employer, ticket prefixes) go in `.git/privacy-deny`, one regex per line, never committed. A public third-party string that looks like an email goes in `.privacy-allow`. Commits use the GitHub noreply address.
- If something leaks anyway, do not just delete it: history and caches keep it. Rotate the token or rewrite the history deliberately, and tell the owner.

## Layout

See [docs/architecture/08-repository-and-migration.md](docs/architecture/08-repository-and-migration.md). Top level: `sim.yaml`, `profiles/`, `benchctl/`, `governor/`, `systemd/`, `images/`, `components/`, `webui/`, `compose/`, `k8s/`, `fidelity-vm/`, `observability/`, `scenarios/`, `docs/adr/`.

## Conventions

- Python 3.11+, Typer for the CLI, Starlette for the web UI (ADR 0008), type hints, `ruff` for lint/format, `pytest` for tests.
- Generated files (compose, `slurm.conf`, Prometheus targets) are derived from `sim.yaml`; never hand-edit them.
- Every component declares a memory budget and priority class (P0-P6).

## Hard rules

- The governor runs on the host, outside everything it may stop, and must not depend on it. By default it runs as the user (`--user-mode`, ADR 0010: docker pause/stop only, best-effort memory lock); the optional root service adds cgroup throttle/reclaim and must `mlockall` at startup and pre-allocate its mitigation path (SPEC GV-8).
- The Web UI never gets root or direct Docker/libvirt access; it only talks to `/run/workbench/gov.sock` (SPEC W-3).
- Do not add macOS-in-VM tooling (license). macOS is an external agent only.
- Do not add real NFS/Lustre or heavy JS frameworks; see SPEC C-11 and W-1.
- Record significant decisions as ADRs in `docs/adr/`.

- **Keep the progress tracker current.** Any PR that changes what is built updates `docs/progress.yaml` (status, how, where, PR; architecture nodes and edges), then `python -m benchctl.progress` regenerates `docs/PROGRESS.md` and `docs/progress.html`. `validate.sh` fails on stale output. See [docs/guides/progress-and-diagrams.md](docs/guides/progress-and-diagrams.md).

## Commands

- `./scripts/validate.sh`: **run before every commit** (coverage floor 90%, `COV_FLOOR=N` to override; needs `coverage`, in the dev extras; secrets scan, compile, ruff, bash -n, YAML/JSON, systemd verify, pytest, governor smoke). `./scripts/validate.sh --install-hook` makes it a pre-commit hook. Set `PYLIBS=<dir>` if tools are installed with `pip --target`.
- `./scripts/real-test.sh`: real-host tests (user cgroups via systemd-run, Docker, a live governor process). Run when touching `governor/`.
- `python -m governor --dry-run --once` (add `--watch-interval S` / `--no-watchdog` for the container security watchdog: new/resolved findings land in the audit log and `status`, skipped from L3 up); `--refresh-interval S` re-discovers containers (late starters get the current level's actions); `--grafana-url URL` publishes audit events as annotations (token only via `$WORKBENCH_GRAFANA_TOKEN`, paused from L3 up); `--signals-file F.json` drives the ladder from a file for tests/tuning.

- `./scripts/benchctl start|stop|open` (they call the internal helper `scripts/benchctl-launch`; `benchctl` is the one controller command, grammar `VERB [RESOURCE] [NAME]`, ADR 0015; add new commands in that grammar, never a new top-level script): one-command launcher (machine check, venv, cluster, web page in the background; pid and log in `compose/generated/ui.pid|ui.log`; `BENCHCTL_UI_PORT` overrides 8088).
- `./scripts/check-machine.sh` (stdlib only, before any build; `benchctl check` once the venv exists; `benchctl/machine.py`) confirms the machine meets the requirements. `./scripts/benchctl doctor | preflight --profile X | status | diag`: environment check, memory pre-flight, governor state, and a masked diagnostics tarball. **Attach `diag` output when reporting or debugging an environment problem.**

- `./scripts/benchctl compose --profile X`: generates `compose/generated/docker-compose.yml` (gitignored) from `sim.yaml` + `profiles/X.yaml` (`groups`, `compute_nodes`, `extends`). Add components in `CATALOG` in `benchctl/compose.py`; never hand-edit the output.
- `./scripts/benchctl measure run <label> [--duration S]`, then `measure report <idle> [<loaded>]`: Phase 0 read-only memory sampling (host, containers, PSS by process, VMs). Results go to `docs/measurements/` (gitignored); copy the suggested budgets into docs/architecture/04-memory-and-profiles.md (section 5), the README profile table and `profiles/*.yaml`.

- Containers must stay unprivileged (no `cap_add`, no `apparmor=unconfined`, no Docker socket) and memory-limited; `benchctl doctor` fails otherwise. Slurm is built from source in `images/node-base` (pinned version + SHA256, ADR 0002).

- Generated secrets (DB password) live only in `compose/generated/secrets` (gitignored); never copy them into tracked files or output. REST access to Slurm: JWT via `scontrol token username=<user>` (not root), see ADR 0003.

- **`./scripts/verify.sh [--full | --only KEYWORD]`: run before asking for a merge** (validate.sh + real-host tests; `--full` adds the live stack lifecycle). Put reusable live-test helpers in `tests/real/helpers.py`, and add a `tests/real` test for every new live behaviour; assert on containers, not only on config. Live tests that read the GPU exporter (127.0.0.1:9400) wait with `helpers.exporter_up` first (it pip-installs pyyaml at start); a test that builds a selection (`benchctl up --cpu-nodes ...`) must call `workbench.forget()` on teardown, or the saved `compose/generated/workbench.yaml` changes every later test's node list.
  - A pass means the pytest summary line says so. Do not judge by the exit code of a pipeline (`verify.sh | tail` returns `tail`'s): use `set -o pipefail` or `${PIPESTATUS[0]}`, or redirect to a file and read the last lines.
  - Errors in the `stack.up` fixtures (every `tests/real` module erroring at once) usually mean the governor refused `up` because host memory is low (GV-9, L2+; browsers and the IDE use several GB). Check `./scripts/benchctl status`, free memory, rerun. That is not a code failure, and it is not a pass: say which it was.
  - The installed root governor acts on every container labelled `workbench.class`. Experiments that start throwaway containers must leave that label off, or the service freezes or stops them.
- User guide: [docs/guides/getting-started.md](docs/guides/getting-started.md); update it when a user-facing command changes (`tests/test_docs.py` checks its commands). The governor needs no sudo to start: `scripts/benchctl-launch` runs it rootless (`python -m governor --user-mode`, ADR 0010); the standing root service `sudo ./scripts/install-governor.sh` is an optional upgrade. Never add a step that needs sudo to the start/build path.
- Governor `admit` (GV-9): `benchctl up`/`scale` ask `/run/workbench/gov.sock` first (`$WORKBENCH_GOV_SOCKET` overrides); governor `scale`/`set-profile` need `--workdir <repo>` and run only at L0-L1.
- Governor `--metrics-addr HOST:PORT` serves Prometheus `/metrics` (the install script binds the docker bridge gateway so the Prometheus container can scrape it; never 0.0.0.0). `tests/real/test_pressure_real.py` drives the ladder against the live stack.
- `./scripts/benchctl <cmd>` runs benchctl with `.venv` without activating it (the venv is per shell). Same as `python -m benchctl.cli`.
- `./scripts/benchctl up --profile X | down | scale N`: pre-flight, ordered start of the compose project (`benchctl/stack.py`), skips components without a Dockerfile, creates the governor's Grafana token once in `compose/generated/secrets/grafana_token`; `down` touches only the `workbench` project.

- `bcm-mock` (127.0.0.1:8081, CMDaemon-style `POST /json`) and `jira-mock` (127.0.0.1:8080, REST v2 subset, `POST /sim/failure`) are stdlib-only; surfaces and gaps in ADR 0005.

- `./scripts/benchctl scenario list | run NAME [--speed N] [--dry-run]`: scenario engine (`benchctl/scenario.py`, ADR 0006) over `scenarios/*.yaml`; add a target in `execute()` and in `ENDPOINTS`/`validate`. gpu-exporter-farm publishes 127.0.0.1:9400 for injection.

- Every mock counts client requests at `GET /sim/stats` (not `/healthz`, `/sim/*` or requests with `X-Workbench-Probe`; the UI sets that on its own polls); `webui/flow.py` turns the counters into the dots feed at `/api/flow` (SSE, opened only while the page is visible; ADR 0009). `benchctl up` now passes `--build` so mock changes are not silently ignored.
- `python -m webui [port]`: Web UI on 127.0.0.1:8088 (ADR 0007), token in `compose/generated/secrets/ui_token`. Backend `webui/app.py` talks to the governor socket only and plays scenarios with `host=False` (no Docker); `webui/cluster.py` reads the mocks and applies node faults through the scenario engine's steps. Keep it under 50 MB RSS (SPEC W-1; ~32 MB now). It uses Starlette, not FastAPI (importing FastAPI alone is ~39 MB, ADR 0008); do not add pydantic-heavy dependencies.

- **UI debugging:** `pip install -e '.[browser]' && .venv/bin/python -m playwright install chromium`, then `.venv/bin/python scripts/ui_shots.py [OUTDIR]` screenshots the dashboard (desktop/phone, light/dark) and checks console errors, overflow, landmarks, the skip link and the theme toggle; read the PNGs. It also fails when the dashboard needs page scrolling at 1280x900 with six nodes, when the phone layout does not stack traffic and list in one column, or when a dialog covers the page while a node is open (the node panel replaces the list in place so the traffic diagram stays visible). `tests/real/test_webui_browser.py` runs the same checks. One message per condition: with the stack or governor down show one notice, hide data that cannot exist, and give a recovery command that needs no sudo (SPEC W-10). UI rules: status never by colour alone, text contrast >= 4.5:1 in both themes, tables stack as cards under 640 px, tap targets >= 40 px, ARIA landmarks, reduced motion respected.

Not yet implemented; as each lands, document it here: `make demo`, `pytest`, `ruff check .`.
