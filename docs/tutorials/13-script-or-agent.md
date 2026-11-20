# 13. Drive it from a script or an agent

**You will:** get machine-readable output, wait for a state, and serve workbench to an AI agent. About 10 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)).

## Steps

1. Ask for JSON from a read.

   ```bash
   ./scripts/benchctl get nodes --json
   ```

2. List the endpoints a client can call.

   ```bash
   ./scripts/benchctl get endpoints --json
   ```

3. Run a scenario in the background, then wait for a result.

   ```bash
   ./scripts/benchctl scenario run node-down --json &
   sleep 2
   ./scripts/benchctl wait node-state cn002 running --timeout 300
   ```

   `node-down` takes `cn002` (not `cn001`) and ends after 40 s. The pause matters: if `wait` starts before the node is off, it returns at once. It prints JSON with `"outcome": "met"` and `elapsed_s` (about 38 here).

4. Read the exit code of `wait`: 0 met, 1 timeout, 2 precondition not met (`echo $?` right after it). A wait that cannot be met, such as `wait node-state cn002 down --timeout 5` on a healthy node, exits 1.

5. Serve the tools to an agent over MCP. It needs the optional extra.

   ```bash
   pip install -e '.[mcp]'
   ./scripts/benchctl serve mcp
   ```

   It speaks MCP over stdin and stdout, so a terminal shows nothing: your agent starts it itself. Exits 0 when stdin closes (`./scripts/benchctl serve mcp </dev/null`). Without the extra it fails; with it, an `initialize` request gets a JSON answer naming the tools and `workbench://agents`.

6. Read the short file an agent should start from: [AGENTS.md](../../AGENTS.md).

## You should see

- Every JSON answer carries `contract_version` and an exit code.
- A destructive verb run with `BENCHCTL_AGENT=1` is refused without `--yes`, for example `WB-NEEDS-YES: This removes the check nosuch and its file. | fix: repeat the command with --yes`.

## Next

- Recipes for agents: `docs/agents/recipes/`.
- Every verb: [Reference](../manual/10-reference.md).
