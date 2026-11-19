# 7. Test tickets

**You will:** get tickets created and moved by a simulated team, tie them to node faults, and make Jira fail. About 10 minutes.

## You need

- A cluster with the Jira mock: `./scripts/benchctl start --with jira`.
- The client connected to Jira ([tutorial 5](05-connect-your-client.md)).

## Steps

1. A drained node gets a ticket that names the cluster and the node, and resuming resolves it. It takes 50 s and ends with `scenario passed` (the node is drained at 0 s, the ticket opened at 5 s, resolved at 50 s).

   ```bash
   ./scripts/benchctl scenario run drain-ticket
   ```

2. Engineers work tickets in the background. This also turns the job load on (about 6 jobs a minute); step 6 stops both. It prints `ticket bot on jira-mock: about one action every 30 s`.

   ```bash
   ./scripts/benchctl load on --tickets --ticket-every 30 --ticket-resolve 120
   ```

3. Speed them up for a test.

   ```bash
   ./scripts/benchctl set ticket-bot --every 10 --resolve 30
   ./scripts/benchctl get ticket-bot
   ```

   It prints `{"every": 10.0, "resolve_after": 30.0}`.

4. Make Jira rate-limit the client: the next 5 calls get 429 (the scenario returns at once; the fault stays until 5 calls were refused).

   ```bash
   ./scripts/benchctl scenario run jira-429
   ```

5. Make Jira go down, then come back: 503 for the next 20 calls, cleared at 60 s.

   ```bash
   ./scripts/benchctl scenario run jira-outage
   ```

6. Stop the background load.

   ```bash
   ./scripts/benchctl load off
   ```

## You should see

- Tickets appear and change status in the client without a click.
- After step 4 the client waits out `Retry-After` instead of retrying at once.
- After step 5 it shows Jira unreachable and recovers by itself.

## Next

- Each ticket verb, and your own ticket steps: [Tickets and their scenarios](../guides/jira-tickets.md).
- The real Jira instead of the mock: [Real Jira](../guides/jira-real.md).
