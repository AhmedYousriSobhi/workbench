# ADR 0061: `benchctl describe workbench --json`: one call for situational awareness

Status: accepted by the owner on 2026-11-13 (issue 603, decisions in issue 613). Built (issues 616 to 628, 633 to 639). Decision A-2 of the agent interface.

## Context
An agent that has just started needs to know what is running, which endpoints to call, which faults and scenarios exist and what to do next. Today that takes several commands (`status`, `get nodes`, `get workbench`, `get scenarios`, the connections table) and parsing of each.

## Decision
- `describe` gains the resource `workbench` (ADR 0015 grammar; `describe node` exists today, `get workbench` shows the saved selection). `benchctl describe workbench --json` returns one compact object: `contract_version`, `state` (running, profile, governor level, available memory), `clusters` with pools, `endpoints` (service, URL, auth kind and a **pointer** to the credential), `faults_active`, `scenarios` (name, summary, minutes), `fault_targets` and `next`.
- No secret is ever a field value. A credential is a command (`benchctl get token`) or a gitignored file path.
- Size: at most 6,500 characters for the default profile (6,000 until issue 548 added three scenarios; ADR 0061 addendum), checked by a test.
- It reads the same sources the web page's Connections view and the governor's read-only commands read; it adds no new access path and no Docker call from the web UI process (SPEC W-3).

## Addendum (2026-11-13, built in issue 624)
To keep to the character budget with all 37 scenarios, `--json` is printed compact and a scenario summary is cut to 30 characters (`get scenarios --json` has the full text). `clusters` lists the Slurm cluster and, when built, the Kubernetes cluster, each with its pools (compute and gpu node counts; the Kubernetes pool label). An endpoint is `id`, `address`, `auth` (none, ssh-key or file) and a `credential` file path when there is one; the working command is in `get endpoints --json`.

## Consequences
- An agent's first call after `start` is one JSON object of about 1,500 tokens.
- The endpoint list reuses the Connections registry, so there is one list of endpoints, not two.

## Addendum (2026-11-14, issue 548)
The three Kubernetes policy scenarios (SC-38 to SC-40) made the default-profile output 6,132 characters. The budget is now 6,500 (about 1,700 tokens), still under the 2,000-token ceiling of AGENTS.md. The owner has not been asked about this number; it is a one-line revert if a smaller budget is wanted, at the price of cutting a scenario row.

Addendum (issue 672): the optional object store adds one endpoint row (about 170 characters), so the budget is 6,700 (about 1,750 tokens), still under the 2,000-token ceiling. The owner has not been asked about this number; it is a one-line revert at the price of dropping that row.

Addendum (issue 678, owner decision 2026-11-14): the budget stays 6,500; the 6,700 above is withdrawn. Scenario rows are `name` and `seconds` only (contract 2.0, `summary` removed); what a scenario does is in `get scenarios --json`. Rule from now on: a new part or scenario fits by moving detail into a drill-down, never by raising the cap, or the token savings of this call disappear.
