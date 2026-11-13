# ADR 0065: Agent output mode: no wasted tokens

Status: proposed, waiting for the owner's approval (issue 603). Nothing here is built. Decision A-6 of the agent interface.

## Context
Colour codes, progress bars, spinners and long logs cost an agent tokens and carry no information.

## Decision
- When stdout is not a terminal, or `BENCHCTL_AGENT=1` is set: no ANSI colour, no progress bar or spinner; an error is one line with a stable code and the exact fix command; a long log goes to a file and only its path is printed.
- Human output on a terminal is unchanged.
- `tests/test_agent_mode.py` checks the three rules.

## Consequences
- Error codes become part of the contract (ADR 0062). Open question for the owner: which code list to start with (the existing `REFUSED`, `Did you mean` messages, plus one per doctor check)?
