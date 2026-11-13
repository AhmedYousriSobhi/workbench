# JSON contract changelog

Every JSON output of `benchctl` carries `contract_version` (`MAJOR.MINOR`); the schemas are in [schemas/](schemas/) (JSON Schema draft 2020-12). Adding a field is a minor bump. Removing or renaming a field, or changing a type, is a major bump. Every change gets a line here (SPEC AG-3, ADR 0062).

## 1.1 (2026-11-13)

Minor, nothing removed. New JSON outputs: `get endpoints` (the Connections registry; the credential is a file path, never a value; `--json` adds the `command`), `get faults` (nodes in the page's states down, fault or warn, plus faults on a service or credential: a Jira failure mode, an expired BMC or Kubernetes certificate, an expired Slurm token, a wrong munge key; each row has `target`, `kind`, `state`, `label`; the Teleport certificate fault is not read back yet), `get traffic` (the mocks' record of client requests, `--since T`) and `scenario run NAME --json` (progress on stderr; stdout is one object with every step and `expect` verdict, or the timeline with `--dry-run`).

## 1.0 (2026-11-13)

First versioned contract. **Major change for scripts:** `get nodes|services|profiles|scenarios|checks -o json` printed a bare list; it now prints `{"contract_version": "1.0", "items": [...]}`, and `describe node -o json` and `get workbench -o json` gain `contract_version` (`get workbench` now nests the selection under `workbench`, and prints `{"workbench": null}` instead of text when there is none). `--json` is an alias of `-o json`.

New: `doctor`, `status`, `plan` and `scenario list` accept `--json`.
