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

## Commands

- `./scripts/validate.sh`: **run before every commit** (secrets scan, compile, ruff, bash -n, YAML/JSON, systemd verify, pytest, governor smoke). `./scripts/validate.sh --install-hook` makes it a pre-commit hook. Set `PYLIBS=<dir>` if tools are installed with `pip --target`.
- `./scripts/real-test.sh`: real-host tests (user cgroups via systemd-run, Docker, a live governor process). Run when touching `governor/`.
- `python -m governor --dry-run --once`; `--signals-file F.json` drives the ladder from a file for tests/tuning.

- `python -m simctl.cli doctor | preflight --profile X | status | diag`: environment check, memory pre-flight, governor state, and a masked diagnostics tarball. **Attach `diag` output when reporting or debugging an environment problem.**

- `python -m simctl.cli compose --profile X`: generates `compose/generated/docker-compose.yml` (gitignored) from `sim.yaml` + `profiles/X.yaml` (`groups`, `compute_nodes`, `extends`). Add components in `CATALOG` in `simctl/compose.py`; never hand-edit the output.
- `python -m simctl.cli measure run <label> [--duration S]`, then `measure report <idle> [<loaded>]`: Phase 0 read-only memory sampling (host, containers, PSS by process, VMs). Results go to `docs/measurements/` (gitignored); copy the suggested budgets into README section 5 and `profiles/*.yaml`.

- Containers must stay unprivileged (no `cap_add`, no `apparmor=unconfined`, no Docker socket) and memory-limited; `simctl doctor` fails otherwise. Slurm is built from source in `images/node-base` (pinned version + SHA256, ADR 0002).

Not yet implemented; as each lands, document it here: `make demo`, `simctl up --profile std`, `pytest`, `ruff check .`.
