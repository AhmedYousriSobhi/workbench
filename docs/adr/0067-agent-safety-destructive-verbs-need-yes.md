# ADR 0067: Agent safety: destructive verbs need `--yes`

Status: accepted by the owner on 2026-11-13 (issue 603, decisions in issue 613). Nothing here is built yet. Decision A-8 of the agent interface.

## Context
An agent can run a destructive command by mistake and cannot be asked 'are you sure' the way a person is.

## Decision
- With `BENCHCTL_AGENT=1`, `down`, `remove ...`, `scale` to fewer nodes and an `apply` that removes nodes refuse without `--yes` and say so in one line.
- The MCP server marks the same tools destructive in their annotations (`workbench_down` at least).
- The governor's admission and levels (SPEC GV-8, GV-9) apply unchanged. An agent must read REFUSED as a decision, not a defect (SPEC NF-6).

## Addendum (2026-11-13, built in issue 618)
`down`, `scale` to fewer nodes, `remove teleport-state`, `remove jira-real-state`, `remove jobs` and `remove check` refuse without `--yes` under `BENCHCTL_AGENT=1` (`WB-NEEDS-YES`); only the first four ever asked a person, so `remove jobs` and `remove check` gained `--yes` and still never prompt. `apply` (and `up`) never remove a running node today (`compose up` runs without `--remove-orphans`; only `down` does), so no gate was needed there; one is required if that changes.

## Consequences
- Behaviour only changes when `BENCHCTL_AGENT=1`; people see no difference.
- Decided: the `--yes` safety rule comes only from `BENCHCTL_AGENT=1`, so existing scripts and cron jobs see no change. Output formatting is separate (ADR 0065): a non-terminal stdout drops colour and progress bars anyway.
