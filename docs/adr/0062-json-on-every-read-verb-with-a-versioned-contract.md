# ADR 0062: `--json` on every read verb, with a versioned schema

Status: proposed, waiting for the owner's approval (issue 603). Nothing here is built. Decision A-3, A-9 of the agent interface.

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
- Open question for the owner: which JSON Schema validator may the tests use without adding a runtime dependency (a dev-extra such as `jsonschema`, or a small local checker)?
