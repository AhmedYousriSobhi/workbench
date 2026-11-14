# ADR 0062: `--json` on every read verb, with a versioned schema

Status: accepted by the owner on 2026-11-13 (issue 603, decisions in issue 613). Built, except `export report` (issue 614; issues 616 to 628, 633 to 639). Decision A-3, A-9 of the agent interface.

## Context
Several read verbs already take `-o json` (for example `get nodes` and `describe node`), but there is no promise about the shape, no schema and no version, so a script or an agent can break silently when output changes.

## Decision
- Every read verb accepts `--json` as an alias of `-o json` (the owner's decision of 2026-11-13): `get nodes|scenarios|checks|endpoints|traffic|faults|capture`, `describe node|workbench`, `status`, `plan`, `doctor`, `scenario run` (final result with every `expect` verdict) and `export profile|report`. Text output stays for people.
- Every JSON output carries `contract_version` (`MAJOR.MINOR`). JSON Schemas live in `docs/agents/schemas/`, one per output, and `docs/agents/CHANGELOG.md` records changes. Adding a field is a minor bump; removing or renaming a field or changing a type is a major bump.
- `tests/test_json_contract.py` validates real outputs against the schemas and runs the privacy scanner over a sample, so no field holds a secret.

## Consequences
- Output shapes are now a public interface and cost a changelog line to change.
- The schemas are also what the MCP tools (ADR 0064) and the recipes (ADR 0063) rely on.
- Exists today: `get nodes|scenarios|checks|capture`, `describe node`, `get workbench`, `status`, `plan`, `doctor`, `export profile`. New: `get endpoints|traffic|faults`, `describe workbench`, `export report`.
- Decided: the tests use `jsonschema`, a pinned, test-only dev-extra (never shipped). Schemas are written in JSON Schema draft 2020-12.
- No JSON output ever contains a secret value, only a pointer to one (for example a file path or the command that prints it); the privacy test enforces it.

## Addendum (2026-11-14, issues 635, 636)
Contract 1.4 adds `get scenario-status`, `clear faults` and `get capture NODE --once --json` (three schemas, see the changelog). `get faults` also reports an expired Teleport identity certificate; it is read from the certificate's own validity dates in the identity file, and no key material is ever read into the output (the privacy test still holds).
