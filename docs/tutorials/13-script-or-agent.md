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
   ./scripts/benchctl wait node-state cn001 running --timeout 300
   ```

4. Read the exit code of `wait`: 0 met, 1 timeout, 2 precondition not met.

5. Serve the tools to an agent over MCP. It needs the optional extra.

   ```bash
   pip install -e '.[mcp]'
   ./scripts/benchctl serve mcp
   ```

6. Read the short file an agent should start from: [AGENTS.md](../../AGENTS.md).

## You should see

- Every JSON answer carries `contract_version` and an exit code.
- A destructive verb run by an agent is refused without `--yes`.

## Next

- Recipes for agents: `docs/agents/recipes/`.
- Every verb: [Reference](../manual/10-reference.md).
