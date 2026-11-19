# 6. Test SSH handling

**You will:** make SSH fail in five different ways and see what your client does each time. About 10 minutes.

## You need

- A running cluster with the client connected ([tutorial 5](05-connect-your-client.md)).

## Steps

1. Read what a scenario does before you run it.

   ```bash
   ./scripts/benchctl scenario run login-node-down --dry-run
   ```

2. The login node refuses connections for 30 seconds.

   ```bash
   ./scripts/benchctl scenario run login-node-down
   ```

3. The login node hangs: no refusal, no answer.

   ```bash
   ./scripts/benchctl scenario run login-node-hung
   ```

4. The host key changes, as after a reinstall.

   ```bash
   ./scripts/benchctl scenario run ssh-host-key-changed
   ```

5. The login node no longer accepts the client's key.

   ```bash
   ./scripts/benchctl scenario run ssh-key-refused
   ```

6. Only the hop to one node fails.

   ```bash
   ./scripts/benchctl scenario run ssh-node-sshd-down
   ```

7. One session per connection.

   ```bash
   ./scripts/benchctl scenario run ssh-session-limit
   ```

## You should see

- Each scenario restores the good state by itself when it ends.
- The client gives one clear message per case: refused, timed out, host key changed, key refused.
- A hang and a refusal read differently. A client that shows the same message for both has a gap.

## Next

- Why each fault looks as it does, and every screenshot: [Testing SSH connections step by step](../guides/ssh-connection-testing.md).
- Through a jump host: [Client setup by component](../guides/client-setup-by-component.md).
