# For AI agents

**In 30 seconds:** workbench is built to be driven by an AI agent. Read one short file, run one command, and everything else is a JSON answer with a version, a schema and a stable error code.

## Do it

```bash
./scripts/benchctl plan --json                  # will it fit? REFUSED is the governor's decision
./scripts/benchctl start
./scripts/benchctl describe workbench --json    # state, endpoints, faults, scenarios, what to do next
./scripts/benchctl wait ready --timeout 180     # block instead of polling
```

Set `BENCHCTL_AGENT=1` first: errors become one line (`WB-KIND-DETAIL: message | fix: COMMAND`), logs go to a file, and destructive verbs ask for `--yes`.

## What is here

| Piece | What it is | Where |
|---|---|---|
| `AGENTS.md` | The one short file (under 2,000 tokens): commands, endpoints, rules. Its endpoint table and verb list are generated from the code | [AGENTS.md](../../AGENTS.md) |
| Recipes | One goal each, with exact commands and what to assert: connect your app, node down, GPU fault, drain to ticket, API errors, SSH failures, Kubernetes, what did my app call, write a scenario, load and memory | [recipes](recipes/connect-your-app.md) (folder `docs/agents/recipes/`) |
| JSON contract | Every read verb takes `--json`; each output has a `contract_version` and a schema | [changelog](CHANGELOG.md), folder `docs/agents/schemas/` |
| Error codes | Every code with its fix command | [ERROR-CODES](ERROR-CODES.md) |
| MCP server | The same verbs as typed tools: `pip install -e '.[mcp]'`, then `benchctl serve mcp` | [verbs guide](../guides/benchctl-verbs.md) |
| Claude Code skill | A short skill that points at all of the above | [skill/SKILL.md](skill/SKILL.md) |

## Install the Claude Code skill

```bash
mkdir -p ~/.claude/skills/workbench
cp docs/agents/skill/SKILL.md ~/.claude/skills/workbench/SKILL.md
```

Skills live in your home directory, not in this repository (`.claude/` is not committed).

## Good to know

> [!TIP]
> The governor can say no. `REFUSED` means starting would risk the machine's memory: close something or pick a smaller profile, and do not retry in a loop.

> [!NOTE]
> The workbench ships three small Prometheus alert rules (`GpuXid`, `GpuOverheat`, `NodeDown`, in `observability/rules.yml`), so `get alerts` and `wait alert` have something to read. They watch the simulated GPU exporter only; the app under test brings its own alerting.

## MCP server: what was checked

`benchctl serve mcp` has fifteen tools (the verbs above plus `scenario_status`, `fault_clear` and `capture_once`). Checked so far:

- **Exercised:** the server process over stdio with the MCP SDK's own client (`tests/test_mcp_stdio.py`): the handshake, the tool list and each tool's annotations (`workbench_down` and `fault_inject` are destructive; the read tools are read-only), and the calls `workbench_describe`, `scenario_run` with `dry_run` and `wait` (a precondition comes back as data, not as a protocol error).
- **Not exercised yet:** an agent runtime configured with the server, and whether it asks the person before `workbench_down`. The annotation is set; whether a given client honours it is the client's behaviour. No client configuration snippet is published until that has been tried.

## Learn more

- [Testing a monitoring client](../guides/client-testing.md)
- [Every command](../guides/benchctl-verbs.md)
- [Requirements for this interface (SPEC 7a)](../../SPEC.md)
