# ADR 0067: Agent safety: destructive verbs need `--yes`

Status: proposed, waiting for the owner's approval (issue 603). Nothing here is built. Decision A-8 of the agent interface.

## Context
An agent can run a destructive command by mistake and cannot be asked 'are you sure' the way a person is.

## Decision
- With `BENCHCTL_AGENT=1`, `down`, `remove ...`, `scale` to fewer nodes and an `apply` that removes nodes refuse without `--yes` and say so in one line.
- The MCP server marks the same tools destructive in their annotations (`workbench_down` at least).
- The governor's admission and levels (SPEC GV-8, GV-9) apply unchanged. An agent must read REFUSED as a decision, not a defect (SPEC NF-6).

## Consequences
- Behaviour only changes when `BENCHCTL_AGENT=1`; people see no difference.
- Open question for the owner: should output not being a terminal also imply agent mode for safety, or only the variable? The proposal is the variable only, so a script run in cron is not changed.
