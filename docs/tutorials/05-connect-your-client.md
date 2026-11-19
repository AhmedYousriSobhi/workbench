# 5. Connect your client

**You will:** import the running cluster into your monitoring client in one step, then break something while it watches. About 10 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)).
- The client installed on the same machine.

## Steps

1. Write a profile of the running cluster.

   ```bash
   ./scripts/benchctl export profile
   ```

2. In the client, import `bench-profile.json`. SSH, Slurm, Grafana and Jira appear already filled in.

3. Give the client the SSH key.

   ```bash
   ./scripts/benchctl ssh-key install
   ```

4. In the client's SSH section pick `~/.ssh/workbench_ed25519`.

5. Paste the Grafana token the export printed. It changes after every `up`.

   ```bash
   cat compose/generated/secrets/grafana_viewer_token
   ```

6. Break something while the client is connected.

   ```bash
   ./scripts/benchctl scenario run node-down
   ```

7. See which surfaces the client really called.

   ```bash
   ./scripts/benchctl get traffic
   ```

8. Attach what happened to a bug report.

   ```bash
   ./scripts/benchctl export test-report last -o test-report.md
   ```

9. Remove the key copy when done.

   ```bash
   ./scripts/benchctl ssh-key remove
   ```

## You should see

- The client lists the nodes, and the node you broke turns down, then recovers.
- `get traffic` names the client's requests: caller, status and latency.

## If it does not work

- The import fails on a format ID: [Export a profile](../guides/export-profile.md#one-time-setting-the-clients-file-format-id).

## Next

- [Test SSH handling](06-test-ssh-handling.md)
- [Test tickets](07-test-tickets.md)
- All endpoints and what each feeds: [Testing a monitoring client](../guides/client-testing.md).
