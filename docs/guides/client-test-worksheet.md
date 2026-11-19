# Client test worksheet

**In 30 seconds:** print this page, set the cluster up with the checklist, then play one scenario per row and write what the client showed. Give each row a verdict: pass, client bug or workbench gap. At the end, copy what was and was not exercised into the issue.

## Do it

```bash
./scripts/benchctl up --cpu-nodes 4 --gpu-nodes 2 --with k8s --with teleport
./scripts/benchctl scenario run node-down
./scripts/benchctl export test-report last -o test-report.md
```

Fill in the setup checklist, then work down the tables. Background and the endpoints: [Testing a client against workbench](client-testing.md). Step-by-step walk-throughs: [tutorials](../tutorials/README.md).

> [!IMPORTANT]
> Use synthetic data only. Never paste a token, a key or a screenshot of real data into this sheet or into an issue.

## Test run

| Field | Value |
|---|---|
| Date | |
| Client name and version (write it here, never in the repository) | |
| Workbench commit (`git rev-parse --short HEAD`) | |
| Profile or selection (`./scripts/benchctl get workbench`) | |
| Parts on (k8s, teleport, jira-real, accounting) | |
| Tester | |

## 1. Setup checklist

Tick each box when its command printed no error.

- [ ] Plan the cluster. It must say `fits`.

  ```bash
  ./scripts/benchctl plan --cpu-nodes 4 --gpu-nodes 2 --with k8s --with teleport
  ```

- [ ] Start it. A refusal means host memory is low: free RAM and run it again.

  ```bash
  ./scripts/benchctl up --cpu-nodes 4 --gpu-nodes 2 --with k8s --with teleport
  ```

- [ ] Only for the real Jira rows: add the part and run its wizard once.

  ```bash
  ./scripts/benchctl start --with jira-real
  ./scripts/benchctl setup jira-real
  ```

- [ ] Write the profile the client imports. Import it in the client.

  ```bash
  ./scripts/benchctl export profile -o bench-profile.json
  ```

- [ ] Install the SSH key where the client can read it. Give the client the path it prints.

  ```bash
  ./scripts/benchctl ssh-key install
  ```

- [ ] Give the client the Grafana viewer token from this file. It changes after every `up`. Never copy it into this sheet.

  ```bash
  ls -l compose/generated/secrets/grafana_viewer_token
  ```

- [ ] Check the governor, the cluster and the web page.

  ```bash
  ./scripts/benchctl status
  ```

- [ ] In the client, trust the host key and connect. SSH, Slurm and Grafana show as healthy.
- [ ] Optional: a live queue for the Slurm panel.

  ```bash
  ./scripts/benchctl load on
  ```

## 2. Judging rule

Decide each verdict with this rule, in this order:

1. The client matches what it should show: **pass**.
2. It does not match, and the real Slurm, Jira, Kubernetes or Teleport would behave as workbench does: **client bug**. Report it in the client's own project.
3. It does not match, and the real product would behave differently: **workbench gap**. File an issue here, with the scenario name and what the real product does.

Never fit workbench to one client's quirks ([client testing](client-testing.md)). Things that look like bugs but are not: [what is real and what is not](client-testing.md#know-what-is-real-and-what-is-not).

Where a row says **decide before the run**, the repository does not state what the client should show. Write your expectation in the cell before you play the scenario. Do not fill it in afterwards.

## 3. Scenarios

Each scenario ends by itself and says `scenario passed`. Add `--dry-run` to see the steps first. A scenario that stops half-way leaves its fault in place: see the warning in [client testing](client-testing.md#test-login-expiry-and-authentication-paths).

### Slurm and SSH

Detail for the SSH rows: [SSH connection testing](ssh-connection-testing.md).

| Scenario | Run | What workbench does | The client should show | The client showed | Verdict |
|---|---|---|---|---|---|
| node-down | `./scripts/benchctl scenario run node-down` | A compute node loses power; BMC and BCM show it down and Slurm drains it, then it recovers | The node down on its next poll (60 s with the Slurm section open and focused, 5 min otherwise); BMC off; GPU metrics gone | | |
| drain-mid-job | `./scripts/benchctl scenario run drain-mid-job` | A node is drained under a running job; the job finishes while the node shows draining, then drained | Decide before the run | | |
| login-node-down | `./scripts/benchctl scenario run login-node-down` | The login node refuses connections for 30 s, then returns with the same host key | The endpoint as refused, then a reconnect on its own | | |
| login-node-hung | `./scripts/benchctl scenario run login-node-hung` | The login node freezes (connections hang, no refusal) for 30 s, then thaws | A timeout, not a refusal; recovery after the thaw | | |
| ssh-host-key-changed | `./scripts/benchctl scenario run ssh-host-key-changed` | The login node presents a different host key, then the old one again | The connection blocked with a changed-key warning, not a quiet reconnect | | |
| ssh-key-refused | `./scripts/benchctl scenario run ssh-key-refused` | The login node answers but refuses the client's key (`Permission denied, publickey`), then accepts it | "Not authorised" (check the key), not "unreachable" | | |
| ssh-node-sshd-down | `./scripts/benchctl scenario run ssh-node-sshd-down` | sshd on `cn001` stops, so `ssh cn001` fails while `cn002` works; then it starts again | That node marked unreachable, not the whole cluster | | |
| ssh-session-limit | `./scripts/benchctl scenario run ssh-session-limit` | The login node allows one session per connection (MaxSessions) | Extra channels queued or retried, not the whole connection failed | | |
| slurmd-restart-mid-job | `./scripts/benchctl scenario run slurmd-restart-mid-job` | `slurmd` is killed and restarted on a node while a real job runs; the job should stay RUNNING | Decide before the run | | |

### GPU

| Scenario | Run | What workbench does | The client should show | The client showed | Verdict |
|---|---|---|---|---|---|
| gpu-xid-storm | `./scripts/benchctl scenario run gpu-xid-storm` | The GPUs of both GPU nodes fall off the bus (XID 79, a critical BMC event); the health check drains each node and a ticket is filed; recovery after 3 min | Decide before the run. The app should open tickets of its own | | |
| fan-failure | `./scripts/benchctl scenario run fan-failure` | Fans fail on a GPU node and it runs hot; the BMC event log shows critical entries and a ticket is filed | Decide before the run | | |
| ib-port-down | `./scripts/benchctl scenario run ib-port-down` | An InfiniBand port goes down on a GPU node and the NIC of another flaps; the health check drains the node and a ticket opens, then both recover | Decide before the run | | |

### Jira (mock and real)

The mock rows need nothing extra. The `jira-real-*` rows need the `jira-real` part (setup checklist) and the client pointed at `127.0.0.1:8083` with `PAT_ALICE` from `compose/generated/secrets/jira-real.env`. The slow, 503 and 429 row runs through a fault proxy: point the client at `127.0.0.1:8084` instead. More: [Tickets and their scenarios](jira-tickets.md), [client test report](client-test-report-real-jira.md).

| Scenario | Run | What workbench does | The client should show | The client showed | Verdict |
|---|---|---|---|---|---|
| jira-429 | `./scripts/benchctl scenario run jira-429` | The mock rate-limits the next 5 API calls (429 with Retry-After) | It backs off and retries | | |
| jira-outage | `./scripts/benchctl scenario run jira-outage` | The mock answers 503 to the next 20 calls, then recovers | Decide before the run | | |
| jira-flap | `./scripts/benchctl scenario run jira-flap` | The mock is switched off and on while tickets exist (refused, rate limited, unavailable), then healthy | Decide before the run | | |
| drain-ticket | `./scripts/benchctl scenario run drain-ticket` | A node drains and gets a ticket naming the cluster and the node; resuming it resolves the ticket | Decide before the run | | |
| jira-real-429 | `./scripts/benchctl scenario run jira-real-429` | The real Jira's own rate limit turns on (3 requests, refill 1 per second), then off at 120 s; each change takes up to a minute to show | Decide before the run. The real Jira sends `Retry-After: 0` | | |
| jira-real-captcha | `./scripts/benchctl scenario run jira-real-captcha` | Wrong-password logins make the real Jira answer 401 then 403 (CAPTCHA lock) for 60 s | Decide before the run. Use a token for the client's account | | |
| jira-real-outage | `./scripts/benchctl scenario run jira-real-outage` | The real Jira hangs for 60 s (container paused), then answers again | Decide before the run | | |
| jira-real-slow-503 | `./scripts/benchctl scenario run jira-real-slow-503` | The real Jira answers slowly, then 503, then 429 with Retry-After 30 s, through the proxy on 8084 | Decide before the run | | |
| jira-real-token-revoked | `./scripts/benchctl scenario run jira-real-token-revoked` | alice's token is revoked (a real 401); a new one is written to `jira-real.env` at 90 s | The client needs the new `PAT_ALICE` value | | |

### Kubernetes

Needs `--with k8s`. The client reads Kubernetes with `kubectl` over SSH on the k-login ([Kubernetes reads](client-testing.md#kubernetes-reads-one-by-one)). With `--login ssh:slurm,ssh:k8s` the k-login is `login2`.

| Scenario | Run | What workbench does | The client should show | The client showed | Verdict |
|---|---|---|---|---|---|
| pod-crashloop | `./scripts/benchctl scenario run pod-crashloop` | A pod enters CrashLoopBackOff and another is OOMKilled | Both surfaced | | |
| k8s-node-notready | `./scripts/benchctl scenario run k8s-node-notready` | A Kubernetes GPU node reports NotReady (KubeletNotReady), then recovers | An alert | | |
| k8s-api-throttled | `./scripts/benchctl scenario run k8s-api-throttled` | The viewer user gets about three seats and no queue; a burst is answered 429, then the level is removed | Decide before the run | | |
| k8s-metrics-down | `./scripts/benchctl scenario run k8s-metrics-down` | metrics-server stops: the metrics API answers 503 and usage disappears; back after 120 s | Decide before the run | | |
| k8s-cert-expired | `./scripts/benchctl scenario run k8s-cert-expired` | The API server restarts with a certificate that ended yesterday for 90 s | Decide before the run | | |
| kube-binding-missing | `./scripts/benchctl scenario run kube-binding-missing` | The viewer tier's Kubernetes binding is deleted for 90 s: Teleport lets the user in, Kubernetes answers forbidden. Needs `--with teleport --with k8s` | Decide before the run. This is the `tsh kube` path; [ADR 0084](../adr/0084-tsh-kube-production-practice.md) says the client does not use it | | |
| Limited kubeconfig outside team-a | `docker exec -u alice workbench-login2-1 sh -c 'kubectl --kubeconfig ~/.kube/limited.yaml get pods -n team-b'` | The limited user may list pods in `team-a` only; the other namespace is forbidden | Decide before the run. Issue 381 asks for a refusal as the client expects | | |

### Teleport

Needs `--with teleport`. Set the client's Teleport cluster up as in [tutorial 9](../tutorials/09-use-teleport.md).

| Scenario | Run | What workbench does | The client should show | The client showed | Verdict |
|---|---|---|---|---|---|
| teleport-outage | `./scripts/benchctl scenario run teleport-outage` | The Teleport proxy stops for 60 s: `tsh` and the web terminal are refused; direct SSH to a key login node keeps working | "Teleport unreachable", without calling the node down | | |
| auth-teleport-cert-expiry | `./scripts/benchctl scenario run auth-teleport-cert-expiry` | alice's identity file is re-signed to live 60 s; the login node then refuses it; signed again at 180 s | The client says login needed | | |
| kube-identity-expired | `./scripts/benchctl scenario run kube-identity-expired` | alice's identity expires after 60 s, so `tsh kube login` fails; signed again at 180 s. Needs `--with k8s` | Decide before the run. This is the `tsh kube` path | | |
| tsh login profile | `tsh --proxy=10.141.255.30:3080 --insecure login --user=alice`, then `tsh --insecure status --format=json` | A real `tsh` profile for alice with a certificate life | A profile with under 300 s left is refused (issue 763); decide the rest before the run | | |
| tsh ssh without a PTY | `tsh --insecure ssh --no-login alice@login2 hostname` | An agentless session to the login node, with no terminal; every run is an audited session | Decide before the run. The exact form is checked in issue 763 | | |

### Storage

The scenarios change what `df`, `lfs df`, `lfs quota` and the `node_filesystem` series show. The `accounting` part is not needed. More: [storage](storage.md).

| Scenario | Run | What workbench does | The client should show | The client showed | Verdict |
|---|---|---|---|---|---|
| disk-full | `./scripts/benchctl scenario run disk-full` | The shared scratch mount fills up (99 percent of bytes, 90 of inodes), then it is emptied | Decide before the run | | |
| quota-exceeded | `./scripts/benchctl scenario run quota-exceeded` | alice is over her quota on `/scratch`; `lfs quota` shows the star and the grace time, then the usage is reset | Decide before the run | | |
| stale-mount | `./scripts/benchctl scenario run stale-mount` | `cn002` loses `/scratch` (a stale handle): `df` fails there and its filesystem series go; then it comes back | Decide before the run | | |

## 4. Evidence

Collect this after each scenario, while the page is still up.

- Who called which mock, and how it ended:

  ```bash
  ./scripts/benchctl get traffic
  ```

- The scenario's steps, results and the mocks' counters, as a file to attach to a bug report. The page must be up while the scenario plays:

  ```bash
  ./scripts/benchctl export test-report last -o test-report.md
  ```

- The client's memory while the stress scenario plays. Use a `--match` that names only the client's own processes, and read the `matched:` line before saving:

  ```bash
  ./scripts/benchctl scenario run client-stress &
  ./scripts/benchctl measure app --match NAME --duration 330 --save
  ```

Where to keep results:
- Keep the filled sheet and the evidence files outside the repository, or in a gitignored folder.
- Commit nothing that holds a token, a key, a host name or real data. The export removes paths, addresses and passwords from its messages, but read it first.
- Screenshots in a report show synthetic data only.

## 5. Close out

1. Stop what you started: `./scripts/benchctl ssh-key remove`, then `./scripts/benchctl down`.
2. In [client testing](client-testing.md), write in the Status column of the row you tested which scenarios were exercised with the client and which were not. Use the words **Exercised** and **Not exercised**.
3. In the issue for the feature, tick the acceptance boxes whose row says pass. Leave the others open and link the issue for each workbench gap.
4. File each client bug in the client's own project. File each workbench gap here, and say in it what the real product does.
5. State in the PR what was and was not exercised with the client (definition of done, item 4).

## If it goes wrong

| Symptom | Try |
|---|---|
| `up` is refused | The governor is protecting the host: free memory, then run it again. That is not a client bug |
| Port 2222 refuses | The checkout may be on a branch without the login node |
| `export test-report` says nothing to export | The page was not up while the scenario played: play it again |
| A scenario stopped half-way | Undo its fault with the matching step ([client testing](client-testing.md#test-login-expiry-and-authentication-paths)) |

## Learn more

- [Testing a client against workbench](client-testing.md)
- [Client setup by component](client-setup-by-component.md)
- [Tutorials: test a monitoring client](../tutorials/README.md)
