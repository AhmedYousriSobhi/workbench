# JSON contract changelog

Every JSON output of `benchctl` carries `contract_version` (`MAJOR.MINOR`); the schemas are in [schemas/](schemas/) (JSON Schema draft 2020-12). Adding a field is a minor bump. Removing or renaming a field, or changing a type, is a major bump. Every change gets a line here (SPEC AG-3, ADR 0062).

## 2.2 (2026-11-14)

Minor, nothing removed. Each `doctor` `tuning` entry gains `persist` and `persist_undo`: the print-only text that keeps the setting across reboots, and its undo (issue 679).

## 2.1 (2026-11-14)

Minor, nothing removed. `doctor` gains `tuning`: one entry per host setting (`zram`, `swappiness`, `thp`) with `current`, `wanted`, `applied`, and the print-only `command`, `effect` and `undo`. doctor never runs them (SPEC P-3, NF-5). In agent mode (`BENCHCTL_AGENT=1`) the text shows one `tuning:` line, only when a setting is not as recommended (issue 679).

## 2.0 (2026-11-14)

Major, one field removed. `describe workbench`: a scenario row is `name` and `seconds`; `summary` is gone so the call stays within its 6,500-character budget as scenarios are added. What a scenario does is in `get scenarios --json` (the drill-down). Nothing else changed (issue 678).

## 1.4 (2026-11-14)

Minor, nothing removed. New JSON outputs: `get scenario-status` (the web page's scenario run: running, name, ok, steps done and each step's status, the `expect` verdicts), `clear faults` (clears every injected node fault; one entry per target) and `get capture NODE --once` (a bounded, one-shot capture: one object with the messages seen, `--seconds` at most 60, `--max-messages`). `get faults` now also reads back an expired Teleport certificate (a row of kind `auth`, target `teleport`, label `certificate of USER expired`). The workbench now ships three default Prometheus alert rules (`GpuXid`, `GpuOverheat`, `NodeDown`), so `get alerts` and `wait alert` have something to read.

## 1.3 (2026-11-13)

Minor, nothing removed. New JSON outputs: `describe workbench` (one call: state, clusters with pools, endpoints with credentials as file paths, active faults, scenarios, fault targets, next steps; at most 6,000 characters, printed compact), `get alerts` (Prometheus alerts, pending or firing; the workbench ships no alert rules, so it is empty until rules are loaded) and `wait ready|node-state|alert|scenario` (one result object: `outcome` met, timeout, failed or precondition; exit 0, 1, 1, 2).

## 1.2 (2026-11-13)

Minor. Each `doctor` check carries a stable `code` (`WB-DOCTOR-...`, the registry is [ERROR-CODES.md](ERROR-CODES.md)). With `BENCHCTL_AGENT=1`, errors are one line `CODE: message | fix: COMMAND` on stderr and `doctor` prints only the checks that need attention (agent output mode, ADR 0065; not part of the JSON contract).

## 1.1 (2026-11-13)

Minor, nothing removed. New JSON outputs: `get endpoints` (the Connections registry; the credential is a file path, never a value; `--json` adds the `command`), `get faults` (nodes in the page's states down, fault or warn, plus faults on a service or credential: a Jira failure mode, an expired BMC or Kubernetes certificate, an expired Slurm token, a wrong munge key; each row has `target`, `kind`, `state`, `label`), `get traffic` (the mocks' record of client requests, `--since T`) and `scenario run NAME --json` (progress on stderr; stdout is one object with every step and `expect` verdict, or the timeline with `--dry-run`).

## 1.0 (2026-11-13)

First versioned contract. **Major change for scripts:** `get nodes|services|profiles|scenarios|checks -o json` printed a bare list; it now prints `{"contract_version": "1.0", "items": [...]}`, and `describe node -o json` and `get workbench -o json` gain `contract_version` (`get workbench` now nests the selection under `workbench`, and prints `{"workbench": null}` instead of text when there is none). `--json` is an alias of `-o json`.

New: `doctor`, `status`, `plan` and `scenario list` accept `--json`.
