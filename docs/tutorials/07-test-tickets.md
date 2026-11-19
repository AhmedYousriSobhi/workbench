# 7. Test tickets

**You will:** get tickets created and moved by a simulated team, tie them to node faults, and make Jira fail. About 10 minutes.

## You need

- A cluster with the Jira mock: `./scripts/benchctl start --with jira`.
- The client connected to Jira ([tutorial 5](05-connect-your-client.md)).

## Steps

1. A drained node gets a ticket that names the cluster and the node, and resuming resolves it.

   ```bash
   ./scripts/benchctl scenario run drain-ticket
   ```

2. Engineers work tickets in the background.

   ```bash
   ./scripts/benchctl load on --tickets --ticket-every 30 --ticket-resolve 120
   ```

3. Speed them up for a test.

   ```bash
   ./scripts/benchctl set ticket-bot --every 10 --resolve 30
   ./scripts/benchctl get ticket-bot
   ```

4. Make Jira rate-limit the client.

   ```bash
   ./scripts/benchctl scenario run jira-429
   ```

5. Make Jira go down, then come back.

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
