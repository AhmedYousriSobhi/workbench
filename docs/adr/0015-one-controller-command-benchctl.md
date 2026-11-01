# ADR 0015: one controller command, `benchctl VERB [RESOURCE] [NAME]`

Status: accepted (issue 90)

## Context
Two commands (`benchctl`, `scripts/benchctl-launch`) and several scripts, each with its own shape, made the tool hard to learn.
Familiar tools solve this with one controller and a fixed grammar: `kubectl get pods`, `docker ps`, `terraform plan`.

## Decision
- One command, `benchctl`, with the grammar `benchctl VERB [RESOURCE] [NAME] [flags]`. Resources are `nodes`,
  `services`, `profiles`, `scenarios`; verbs are `start`, `stop`, `open`, `up`, `down`, `scale`, `get`, `exec`, `logs`,
  `scenario`, `status`, `check`, `doctor`, `preflight`, `diag`, `ssh-key`, `compose`, `measure`.
  Planned in the same grammar: `describe` (issue 93), `plan`/`apply` (92), `export` (91).
- `./scripts/benchctl` runs it without activating the venv; the venv also installs a `benchctl` entry point.
- `start`, `stop`, `open` call `scripts/benchctl-launch`, which keeps the launcher logic (machine check, venv, governor, web page).
- The old names (`simctl`, `scripts/simctl`, `scripts/opti`, the `OPTI_*` variables and the `hpcsim` names of the Docker
  project, labels, socket, systemd units and metrics) were first kept as aliases and removed in #111. The Python package,
  the Docker project, the image names, the labels, `/run/workbench`, the systemd units and the metric prefix are all
  `benchctl` or `workbench` now. See the migration note in the manual's troubleshooting page.
- Docs and tests name only `benchctl`; a test fails if a tracked file still uses an old name.
- Tab completion comes from Typer's shell handlers, installed by `benchctl completion install` (#105; Typer's own `--install-completion` is switched off because it needs `benchctl` on PATH): the script goes to `~/.local/share/workbench`, one line to the rc file, and a `~/.local/bin/benchctl` link; `remove` undoes all three. Completers (`benchctl/completion.py`) read only `sim.yaml`, `profiles/` and `scenarios/`, never Docker. Wrong values get `Did you mean` and the valid list, exit 2.

## Consequences
The command name is settled (issue 89 was closed without a rebrand). The old names no longer work.
