# ADR 0015: one controller command, `benchctl VERB [RESOURCE] [NAME]`

Status: accepted (issue 90)

## Context
Two commands (`simctl`, `scripts/opti`) and several scripts, each with its own shape, made the tool hard to learn.
Familiar tools solve this with one controller and a fixed grammar: `kubectl get pods`, `docker ps`, `terraform plan`.

## Decision
- One command, `benchctl`, with the grammar `benchctl VERB [RESOURCE] [NAME] [flags]`. Resources are `nodes`,
  `services`, `profiles`, `scenarios`; verbs are `start`, `stop`, `open`, `up`, `down`, `scale`, `get`, `exec`, `logs`,
  `scenario`, `status`, `check`, `doctor`, `preflight`, `diag`, `ssh-key`, `compose`, `measure`.
  Planned in the same grammar: `describe` (issue 93), `plan`/`apply` (92), `export` (91).
- `./scripts/benchctl` runs it without activating the venv; the venv also installs a `benchctl` entry point.
- `start`, `stop`, `open` call `scripts/opti`, which keeps the launcher logic (machine check, venv, governor, web page).
- The old names (`simctl`, `./scripts/simctl`, `./scripts/opti`) stay as aliases. The Python package keeps its name
  `simctl` (renaming it would touch every import for no user benefit).
- Docs and tests name only `benchctl`; a test fails if a guide tells a user to type an old name.
- Tab completion comes from Typer (`benchctl --install-completion`); node arguments complete from `sim.yaml`.

## Consequences
The command name is a brand choice and may change again with the rebrand (issue 89); because every doc line says
`benchctl` and the wrapper is one file, a rename is a search-and-replace plus a new alias.
