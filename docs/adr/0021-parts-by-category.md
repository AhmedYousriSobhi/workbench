# 0021. Optional parts are chosen by category

Status: accepted (issue 184)

## Context
`benchctl start` with no flags runs the `min` preset, which has none of the optional parts. Choosing them meant knowing
part names (`--with accounting`), and the web sheet listed them flat. Users asked for flags that say what a part is for
(`--ticketing jira`) and one switch for everything, with Tab completion to guide the command.

## Decision
- **One registry**, `workbench.CATEGORIES`: category -> (what it groups, item -> part). Every optional part is in exactly
  one category (a test enforces it). Flags, completion, help text, error messages and the web sheet's grouping all read it.
- **Categories:** `workload` (slurm; kubernetes refused: not built), `accounting` (slurmdbd), `monitoring` (prometheus,
  grafana: always on, accepted), `ticketing` (jira), `hardware` (bmc, bcm), `access` (teleport), `simulation` (scenarios,
  load). `--hardware` rather than `--management`: a BMC is hardware, BCM is the manager of it.
- **Values** are comma separated or repeated; `all` means every buildable item of the category. `--addons` is every
  optional part. They add to the default parts like `--with`; `--without` still removes. `--with/--without` stay.
- **Where:** `start`, `up`, `apply` and `plan`. `start` expands them in Python and forwards plain `--with PART` to the
  launcher, so the shell launcher needs no knowledge of categories. A selection on `start` includes the default parts,
  so a flag that adds nothing still builds a selection and not the `min` preset.
- **Completion** reads the registry only (no Docker): flag names, items, and items after a comma without repeating one;
  unbuilt items (kubernetes) are never offered. An unknown item gets one message with a close match and the valid list.

## Consequences
- A new part is added to `PARTS`, `PART_INFO` and one category; nothing else changes.
- Always-on items (slurm, grafana, prometheus) are accepted but do nothing; making them optional would change the
  governor's memory floor and is a separate decision.
- Plain `start` is unchanged (`min`). Remembering the last cluster is a separate issue.
