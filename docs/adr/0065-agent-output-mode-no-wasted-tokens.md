# ADR 0065: Agent output mode: no wasted tokens

Status: accepted by the owner on 2026-11-13 (issue 603, decisions in issue 613). Built (issues 616 to 628, 633 to 639). Decision A-6 of the agent interface.

## Context
Colour codes, progress bars, spinners and long logs cost an agent tokens and carry no information.

## Decision
- When stdout is not a terminal, or `BENCHCTL_AGENT=1` is set: no ANSI colour, no progress bar or spinner (safe for scripts, and it saves tokens). With `BENCHCTL_AGENT=1`: an error is one line with a stable code and the exact fix command, and a long log goes to a file with only its path printed.
- Human output on a terminal is unchanged.
- `tests/test_agent_mode.py` checks the three rules.

## Consequences
- Error codes become part of the contract (ADR 0062). Decided: start from the existing `REFUSED` and `Did you mean` messages plus one code per `doctor` check, under three rules:
  - one stable prefix scheme, `WB-<KIND>-<DETAIL>` (for example `WB-REFUSED-MEMORY`, `WB-UNKNOWN-VERB`, `WB-PART-NOT-RUNNING`);
  - one registry file that the tests check against the code;
  - every code carries its fix command.
