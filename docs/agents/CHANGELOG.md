# JSON contract changelog

Every JSON output of `benchctl` carries `contract_version` (`MAJOR.MINOR`); the schemas are in [schemas/](schemas/) (JSON Schema draft 2020-12). Adding a field is a minor bump. Removing or renaming a field, or changing a type, is a major bump. Every change gets a line here (SPEC AG-3, ADR 0062).

## 1.0 (2026-11-13)

First versioned contract. **Major change for scripts:** `get nodes|services|profiles|scenarios|checks -o json` printed a bare list; it now prints `{"contract_version": "1.0", "items": [...]}`, and `describe node -o json` and `get workbench -o json` gain `contract_version` (`get workbench` now nests the selection under `workbench`, and prints `{"workbench": null}` instead of text when there is none). `--json` is an alias of `-o json`.

New: `doctor`, `status`, `plan` and `scenario list` accept `--json`.
