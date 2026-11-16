<p align="center"><img src="../assets/art/guide-client-testing-hero.svg" alt="A loop: up, point, break, watch, down" width="720"></p>

# Testing a monitoring client against workbench

**In 30 seconds:** bring the cluster up, point your client at it, run a scenario, and compare what the client shows with what the scenario did. Know which parts are real and which are simulated before you call something a client bug.

## Do it

```bash
./scripts/benchctl up --profile min        # refused when host memory is low: free RAM and run it again
./scripts/benchctl status                  # governor, cluster and web page at a glance
./scripts/benchctl scenario run node-down  # break something while the client is connected
./scripts/benchctl down                    # when done; always stop what you started
```

The client connects to workbench as it would to a production site: SSH to a login node, Slurm commands, Grafana GPU metrics, Jira search, Redfish BMCs. You then play scenarios (faults, outages, workloads) and watch how the client reacts.

> [!IMPORTANT]
> Never fit workbench to one client's quirks. The client is general-purpose, so make the simulated service behave like the real one, and report a mismatch against the real product's behaviour.

The cluster has partitions `cpu` (4 nodes) and `gpu` (2 nodes, 8 `h200` GPUs each) and the users `alice` and `bob`. `up` prints the `ssh:` line and the names of the secret files.

Shortcuts: import the whole cluster from one file with `benchctl export profile` ([export profile](export-profile.md)), or set up one client component at a time ([client setup by component](client-setup-by-component.md)).

## Find what the client needs

Every part has a fixed private address (`10.141.x.x`, standard ports; [ADR 0018](../adr/0018-fixed-addressing-plan.md)). This machine reaches them because Linux routes the Docker network; the ports published on `127.0.0.1` are the fallback. `benchctl up` writes the secrets into `compose/generated/secrets/` (gitignored: never print or commit them).

| Client feature | Endpoint | Credential | Status |
|---|---|---|---|
| SSH terminal, SFTP (a Files view: `sftp alice@127.0.0.1` lists the home directory; the stock `sftp-server` as the user, no chroot), Slurm (`squeue`, `sinfo`; `sacct` needs the `accounting` part: on in a default workbench, `--with accounting` otherwise; without it sacct says accounting storage is disabled), port forwarding | `10.141.255.254:22` (fallback `127.0.0.1:2222`) | user `alice` or `bob` (from `sim.yaml`), key `secrets/ssh/id_ed25519` | merged (ADR 0011); compute nodes `cn001`... and `gpu001`... have sshd too, reached from the login node (ADR 0014). With several login nodes (`--login ssh,teleport`, ADR 0023) the table row is the first one that takes a key: `login2`... have addresses .252, .251, ... and ports 2223, ...; a `teleport` node takes no key (test the Teleport row against it) |
| GPU usage through Grafana `/api/ds/query` | Grafana `10.141.255.21:3000` (fallback `127.0.0.1:3000`), datasource uid `prometheus`, host label `Hostname` | Viewer token in `secrets/grafana_viewer_token` | merged |
| Jira search (`/rest/api/3/search/jql`, fallback `/rest/api/2/search`), `/myself`, `/field`; Confluence pages (`/rest/api/search?cql=`, Cloud style `/wiki/rest/api/search`) on the same address | `http://10.141.255.11:8080` (fallback `127.0.0.1:8080`) | none in mock mode | merged (ADR 0005) |
| BMC Redfish over TLS, certificate pinning | one address per node, `10.148.0.N:443` / `10.148.100.N:443` (fallback: one listener `127.0.0.1:8443`, node chosen by the `Host: bmc-<node>` header) | `admin` and the password in `secrets/bmc_credentials` | merged (ADR 0012); plain HTTP on `:8000` also remains. Inside the cluster network (login node, containers) the BMCs also answer on the standard `bmc-<node>:443`; on the host only `8443` is published (443 needs root) |
| BCM (`cmsh` on a head node) | the login node is the head node: `cmsh -c "device; list"` over the same SSH connection | as for SSH | built (ADR 0013); read-only, with a small interactive console (modes, list, status); node state follows bcm-mock and scenarios; the head node's events log `/var/spool/cmd/events.log` gets a line per node state change ( format from the documented shape, not verified against a real CMDaemon) |
| Teleport (`tsh login`, `tsh ssh`, ssh through the proxy) | proxy `10.141.255.30:3080` or `teleport.workbench.test:3080` (self-signed; `--insecure`), optional part: `benchctl up --with teleport` | identity file per user in `secrets/teleport/identity/`, or a password and one-time code set up through `benchctl add invite <user>` | built, review (ADR 0020): agentless OpenSSH nodes; with the k8s part also `tsh kube` (ADR 0083, docs/guides/teleport-kube.md); not simulated: agent extras, web/DB access. Your `tsh` major must not be newer than `teleport.version` |
| Kubernetes (`kubectl` over the SSH session: `get nodes\|pods\|events`, `get --raw /readyz`, `auth can-i`, `describe`, `logs`) | the login node: `kubectl` as `alice` or `bob` with `~/.kube/config` (read-only, cluster-wide) and `~/.kube/limited.yaml` (namespace `team-a` only), both mode 600 and owned by the user; the API server `https://10.141.255.40:6443` (fallback `127.0.0.1:6443`); optional part: `benchctl up --with k8s` | the kubeconfigs above (tokens in `secrets/k8s/`); the host copy `secrets/k8s/admin.kubeconfig` is for you, never for a client | built, review (ADR 0034): a real API server, scheduler and controller manager; the nodes (`kcn001`, `kgpu001`...) are KWOK fakes with `nvidia.com/gpu` and GPU Feature Discovery labels, so pods never really run: no real pod logs (`kubectl logs` has nothing to show), no `exec`, no metrics-server (the client treats it as optional); failing pods get their status from KWOK stages. With `--login ssh:slurm,ssh:k8s` the Kubernetes entry is the k-login (`login2`) and the Slurm entry the s-login. **Exercised:** `benchctl export profile` run through the client's own profile importer (the Kubernetes entry gets `k8s`: enabled, interval, workload signals, `poolLabel pool`; host and kubeconfig stay yours), and the commands it runs (`get nodes -o json`, `get pods -A`, `get events -A`, `get --raw /readyz?verbose`, `/version`, `auth can-i`, `config view --minify`, `describe pod`, Deployments, Jobs, PVCs, DaemonSets, ResourceQuotas, HPAs) as `alice` over SSH on the k-login: all answer; the metrics API answers 404, which the client treats as optional. **Not exercised:** the client's own screens (Discover step, Nodes and GPUs tabs, notifications): they need the desktop app, run by you |

If port 2222 refuses connections, the checkout is probably on a branch without the login node. That is not a client bug.

## Point the client at it

- **Execution target:** set a node such as `cn001` as the scheduler's execution target. The client runs `ssh <node> <command>` from the login node; no keys to set up, because the login node already trusts the nodes.
- **SSH:** host `10.141.255.254`, port `22` (fallback `127.0.0.1` / `2222`), user `alice`. Many clients read private keys only from `~/.ssh`. Run `./scripts/benchctl ssh-key install`: it copies the generated key to `~/.ssh/workbench_ed25519` (mode 600) and prints the path to give the client. Run `./scripts/benchctl ssh-key remove` when done. Alternatively load the key into a throwaway agent: `eval "$(ssh-agent -s)"; ssh-add compose/generated/secrets/ssh/id_ed25519`.
- **Host key:** the first connection needs the client's trust-on-first-use confirmation. `up` keeps the same host key across restarts, so the pin stays valid.
- **Slurm:** the client's refresh runs fixed `squeue` and `sinfo` commands. Submit work as `alice` so there is something to show: `sbatch --wrap="sleep 300" -p cpu` (add `-p gpu --gres=gpu:2` for GPUs).
- **GPU panel:** set the datasource uid to `prometheus`, the host label to `Hostname`, and the Viewer token. Per-job GPU usage appears only while the SSH user has a running job, and the GPUs of that job read busy (the others idle).
- **Jira:** base URL `http://10.141.255.11:8080`. A client that refuses plain HTTP for a non-local host uses the fallback `http://127.0.0.1:8080`.
- **Drain a node** for the node-issues view: `docker exec <slurmctld container> scontrol update nodename=cn004 state=drain reason="test"`, then `state=resume`.

SSH connection handling (hung, down, changed host key, session limit), step by step with screenshots: [SSH connection testing](ssh-connection-testing.md).

## Break a node and watch the client

A scenario is a YAML file in `scenarios/` ([scenarios and faults](../manual/06-scenarios.md)). Steps run at offsets and can target `bmc-farm`, `bcm-mock`, `gpu-exporter`, `jira`, `slurm`, `ssh`, `k8s` and `auth`.

```yaml
name: gpu-node-overheats
description: A GPU node fans out and overheats, then Slurm drains it and it recovers
steps:
  - {at: 0s,  target: bmc-farm, do: {nodes: [gpu002], fan_fail: 3}}
  - {at: 5s,  target: bmc-farm, do: {nodes: [gpu002], ramp: {to: 92, over: 30s}}}
  - {at: 20s, target: slurm,    do: {drain: [gpu002], reason: "overheating"}}
  - {at: 60s, target: bmc-farm, do: {nodes: [gpu002], clear: true}}
  - {at: 60s, target: slurm,    do: {resume: [gpu002]}}
```

```bash
./scripts/benchctl scenario list
./scripts/benchctl scenario run gpu-node-overheats --dry-run   # validates node names and targets, changes nothing
./scripts/benchctl scenario run gpu-node-overheats --speed 4   # four times faster
```

Run it while the client is connected and compare what the client shows (node state, reasons, alerts, GPU temperature and XID metrics, BMC events, Jira failures) with what the scenario did. A new kind of fault belongs in the scenario engine's targets, not in ad hoc `docker exec` commands. A scenario can also be run by its reference: `benchctl scenario run SC-02`.

### Take a node down so Slurm notices

**Power off** on the page (or `benchctl set node cn001 off`, which does the same):
- sets the BMC, BCM and GPU metrics to off;
- freezes the node's container, so its `slurmd` stops answering.

Then:
- `slurmctld` marks the node `down*` (Not responding) by itself after about 30 s.
- The BMC reports it off, and its GPU metrics disappear, as on a real dead node.
- A client shows it on its next poll: 60 s with the Slurm section open and the window focused, 5 min otherwise.

**Power on** (`set node cn001 on`, or the page's button) reverses all of that and thaws the container; Slurm returns the node after its next answer. Use `set node NAME down` only to set the state by hand. Scenarios behave the same: one `bmc-farm` step with `power: "Off"` takes the node down on the BMC, BCM, GPU metrics and in Slurm, also when the scenario is played from the page.

> [!NOTE]
> The timings above (`SlurmdTimeout=15`, health check every 30 s) are the defaults, and what you test a client against. The repository's own live tests shorten them with `WORKBENCH_TEST_TIMERS=1` (10 s and 15 s) and `up` prints a note when that is on. Never set it while testing a client.

### Node health check: what drains a node

Every `slurmd` runs `/usr/local/sbin/nhc` (source `images/node-base/nhc`, a small stand-in for LBNL NHC) three ways: every 30 s (`HealthCheckProgram`), before each job starts on the node (`Prolog=nhc-prolog`) and after it ends (`Epilog=nhc-epilog`). It reads the exporter farm and **drains the node** with a reason starting `NHC:` when:

| Check | Fault to inject | Reason in `sinfo -R` |
|---|---|---|
| GPU missing (fewer series than the GRES) | none yet (a fault action is not modelled) | `NHC: GPU count 7 of 8` |
| GPU XID error | **GPU error (XID 79)** | `NHC: GPU XID 79` |
| InfiniBand port down (one HCA per GPU, one port on a CPU node) | **InfiniBand port down** | `NHC: IB port down: mlx5_0` |
| NIC link flapping (`node_network_carrier_changes_total` keeps rising) | **NIC flapping** | `NHC: NIC eth0 flapping (4 link changes)` |

The list of checks is not fixed: add your own, switch one off or reorder them from the command line, by editing files or in the page's **Health checks** sheet. Every change is validated and live within 30 s ([NHC checks](nhc-checks.md)).

- The periodic run drains within about 30 s and resumes a drain it made itself once the fault is cleared (never one an operator set).
- The **prolog** fails an unhealthy node, so Slurm requeues the job instead of running it there.
- The **epilog** drains a node that a job left unhealthy, so the next job is not scheduled on it.
- A node that publishes nothing (powered off) and an unreachable exporter are not drained.
- A cluster built before the health check needs `benchctl up` once: it rebuilds the node image (the prolog and epilog scripts are in it) and restarts `slurmctld`. Without the rebuilt image a job start fails on a missing prolog and drains the node.

**Tickets for what Slurm does.** `slurmctld` also runs a watcher (`images/slurm-ctl/node_watch.py`) that polls `sinfo` every 10 s.
- A node that turns DOWN or drained files a ticket: kind `slurm`, or a comment on the node's open ticket, so a Power off stays one ticket.
- A node that comes back is resolved.

That covers a node Slurm drained by the health check or by hand, and a node that went down by not answering, which no page button or scenario step reports.

### Switch topology

With a `topology:` in `sim.yaml` ([topology](topology.md)), `scontrol show topology` lists the switches. `benchctl scenario run switch-leaf-down` (SC-35) drains one leaf's nodes for 90 s and resumes them. Check that the client shows the switch tree and the reason `switch leaf1 down`.

### Slow or lossy links inside the cluster

`benchctl scenario run netem-slow-link` (SC-46) slows one compute node's own network with `tc netem`.
- The first compute node gets 200 ms of delay and 5 % loss for 60 s.
- slurmctld, and a client polling `sinfo`, see slow or lost replies.
- The TTL ends the fault, and the scenario clears it at 90 s as well.

Check before you run it:
- The optional root governor service must run (`sudo ./scripts/install-governor.sh`).
- Memory must be at level L0-L1.
- It is for the CLI and scenarios only, never the page.

More: [network faults](network-faults.md).

## Test login, expiry and authentication paths

These faults give the client real errors from the real components, for its "login needed", "certificate expired" and "unauthorised" paths. They need the host CLI; the page cannot play them.

| Scenario | What the client meets | Put right by |
|---|---|---|
| `auth-slurm-token-expiry` (SC-30) | slurmrestd refuses the token after its 60 s (HTTP 511, as for any bad token; only a request with no token gets 401); the token is in `compose/generated/secrets/slurm_token` (`cat` it, it is not printed) | ask for a new one: `docker exec workbench-slurmctld-1 scontrol token username=alice` |
| `auth-teleport-cert-expiry` (SC-31) | the identity file `compose/generated/secrets/teleport/identity/alice` lives 60 s, then the login node refuses it | the scenario signs it again at 180 s |
| `auth-munge-wrong-key` (SC-32) | `cn001` is rejected by slurmctld (`Invalid credential`); `sinfo` shows it as `idle*` (not responding), flapping with `idle`, never `down` (slurmd still answers each ping, with an authentication error) | the scenario puts the node's own key back at 120 s (`munge_restore`) |
| `bmc-tls-expired` (SC-33) | `certificate has expired` on the BMC TLS ports (8443, 443, each node's address); same key and name, so a client that pins by key still connects | back at 60 s |
| `k8s-cert-expired` (SC-34) | `certificate has expired` from `kubectl` and any client that checks dates; the API server restarts twice (10 to 20 s each) | back at 90 s |

> [!WARNING]
> A scenario that stops half-way leaves the fault in place. Undo it with the matching step, written as a one-step scenario: `{auth: {munge_restore: [cn001]}}`, `bmc_tls_expired: false`, `k8s_cert_expired: false` or `teleport_cert_restore: [alice]`.

## Give the client a live queue

`benchctl load on` starts simulated users: `alice` and `bob` submit cpu jobs and 1 to 4 GPU jobs at random, so the Slurm panel has running and pending jobs and a node going down hits real work. `benchctl load off` stops it. `benchctl submit NODE` (or the node panel's Workload card) runs one job on a chosen node. A job pinned to a node that is down stays pending (*ReqNodeNotAvail*) until the node is back or you `scancel` it.

For a more lifelike queue add two opt-in switches (same simulator, same 20-pending cap, no extra container or memory):

```bash
benchctl load on --personas --day 600
```

`--personas` gives each user the job mix of the `persona:` in `sim.yaml`:
- `alice` (ml-trainer) submits mostly 1 to 4 GPU jobs, long, with 4 or 8 CPUs per task.
- `bob` (mpi-researcher) submits mostly cpu jobs.
- About 10% of jobs fail. They end early with exit code 1, so `squeue -t all` and `sacct` show FAILED.
- 5 to 10% are cancelled by their owner a few seconds after starting (CANCELLED).

`--day 600` squeezes a simulated day into 600 real seconds. Arrivals follow a curve: busiest at 14:00, quiet at night, and the ml-trainer peaks at 22:00. The queue grows and drains within minutes.

Add `--seed N` for repeatable arrivals.

### Add engineers working tickets

`benchctl load on --tickets` also starts the ticket bot on the login node. It works only the alert tickets (project OPS, label `alert`, opened by `POST /sim/alerts` or a node fault) in **jira-mock**, never a real Jira. For each open alert ticket:

- about 15% stay unassigned;
- the rest are assigned to an engineer (bob, carol, dave or erin), moved to In Progress, get a comment, and are Resolved after `--ticket-resolve` seconds (default 600);
- about 10% of resolved tickets are reopened and worked again.

It acts about once every `--ticket-every` seconds (default 120, 10 to 3600) and, with `--day`, follows the same daily rhythm as the simulated users. The governor's L1 flag pauses it, and `benchctl load off` stops it. Its calls arrive with the User-Agent `workbench-ticket-bot`, so the Traffic tab names it. The flows and the default rate are a recommendation the owner has not confirmed; both are settings.

```bash
benchctl load on --tickets --ticket-every 30 --ticket-resolve 120
```

Change it while it runs. The bot re-reads its settings every cycle, so a change applies within one gap:

```bash
benchctl set ticket-bot --every 10 --resolve 30      # a fast test
benchctl set ticket-bot --ignore 0.5 --reopen 0.3    # half stay unassigned, 30% reopen
benchctl get ticket-bot                              # what is in force
```

From the page: Menu, then **Ticket bot**. The sheet shows the values last set, takes the same four numbers and sends them through the governor (the page has no Docker), so the governor must run with `--workdir` (the normal `benchctl start`). Empty boxes stay as they are.

Ranges: `--every` 10 to 3600 s, `--resolve` 10 to 86400 s, `--ignore` and `--reopen` 0 to 1. Only the values you give change. A value out of range is refused, and a damaged settings file keeps the last good values. The next `load on --tickets` starts from its own options again.

## Stress test: everything at once while the client runs

`benchctl scenario run client-stress` (5 minutes, [scenarios/client-stress.yaml](../../scenarios/client-stress.yaml)) does this to the client:

- floods five nodes with jobs, raises GPU XID errors and fan failures, heats a GPU node;
- powers off and drains `cn004`, files 12 tickets;
- makes the Jira mock answer 429, slow and 503, then clears everything.

With the client connected to the real Jira (`jira-real`), run `client-stress-real` instead. It has the same nodes, GPUs, heat and jobs, but adds:
- Jira's own rate limit (429, about 75 to 180 s);
- a CAPTCHA lock for one user (use a personal access token for the client's own account, or it is locked out too);
- a 30 s hang.

It files its 12 tickets on the real Jira. Only tickets made in the last 10 minutes are counted, so a repeat run does not add up.

`client-stress` hits `jira-mock` (8080). `client-stress-real` hits `jira-real`.

Run it with the client open and measure the client at the same time. Use a `--match` that names only the client's own processes, such as its install path; a loose word such as `name` matches unrelated programs. The tool prints `matched: <program> xN (MB)` before it samples and warns when more than 4 different programs match. Read that line before `--save`.

```bash
./scripts/benchctl scenario run client-stress &
./scripts/benchctl measure app --match NAME --duration 330 --save
```

To see the five minutes before you play them, add `--dry-run`. The first steps start jobs on every node, then the GPU, fan and heat faults follow:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/snapshots/scenario-client-stress-dark.svg">
  <img src="../assets/snapshots/scenario-client-stress-light.svg" alt="Terminal: ./scripts/benchctl scenario run client-stress --dry-run (prints: SC-01 client-stress: 24 steps, 270 s, targets bcm-mock, bmc-farm, gpu-exporter, jira, slurm)">
</picture>

*client-stress: the first steps of five minutes*

**With the real window, step by step:**

1. Bring the workbench up with the real Jira: `benchctl start --with jira-real`, then `benchctl setup jira-real` once (`benchctl status` shows the licence end).
2. `benchctl export profile -o profile.json`, then `benchctl ssh-key install` (the client accepts only a key under `~/.ssh`; run `benchctl ssh-key remove` at the end).
3. Start the client in its dev mode with a throwaway config directory (`XDG_CONFIG_HOME=<empty dir>`) and `--remote-debugging-port=9222`. A fresh config asks for a local account (any name, no password), then offers to claim the existing profile: choose it, confirm, Continue.
4. Create the cluster through the client's own API from the profile file: the entry for the login node that takes a key, the key at `~/.ssh/workbench_ed25519`, the Grafana viewer token from `compose/generated/secrets/grafana_viewer_token`, and for the real Jira `http://127.0.0.1:8083` with `PAT_ALICE` from `compose/generated/secrets/jira-real.env`. Read the secrets from the files inside the script; never print them.
5. In the client, trust the host key and Connect. Check that SSH, Slurm, Grafana and the Jira section are green.
6. Measure idle (`measure app --duration 40`), then run the scenario, the measurement and a loop that opens every view (Grafana, Slurm with its Jobs, Nodes, Partitions and GPUs tabs, Storage, Tickets and docs, Notes) together.
7. Stop the client, remove the throwaway config and the installed key.

Measured once this way (a desktop Electron client, one cluster, a terminal, the real Jira, every view opened in turn):
- 469 MB idle;
- 664 MB peak and 555 MB typical during the scenario;
- 8 processes;
- the machine reserve set to 800 MB.

A first attempt whose clicks missed (the client only polled) peaked at 539 MB, which is why the views must be opened.

One session and one cluster is a floor for several clusters or terminals, not a limit.

## See which surfaces the client used

Each mock lists the paths it served and the ones it did not know, at `GET /sim/paths`. It records only the method and the path shape (ids, issue keys and node names show as `{id}`, query values are dropped, no bodies or headers); for BCM the call is `POST /json service.call`. Health checks, `/sim/*` calls and the page's own polls are not counted. The counters start at zero when the container starts.

```bash
curl -s http://127.0.0.1:8000/sim/paths   # bmc-farm (Redfish)
curl -s http://127.0.0.1:8081/sim/paths   # bcm-mock
curl -s http://127.0.0.1:8080/sim/paths   # jira-mock
```

Run the client against the stack once, then read `paths` (what it called and how often) and `unknown` (what it asked for that workbench does not serve: a 404, or for BCM an unknown call). That list is where the scope of the Redfish, BCM and Kubernetes surfaces starts. The Prometheus-style exporters and the Kubernetes API are not covered. When you report a gap, state what the client run did and did not exercise.

### Who asked, how it ended and how long it took

`GET /sim/traffic` (the same three mocks) adds, per **caller** and path shape, how many answers were 2xx, 4xx and 5xx, a latency histogram, and the last 400 requests with their time. The caller is the request's `User-Agent` product token only (for example `curl` or `python-requests`; no version, no other header value, at most 24 characters, `unknown` when there is none). `?since=T` (seconds since the epoch) returns only the newer requests. Asking for it is not counted as client traffic.

```bash
curl -s http://127.0.0.1:8080/sim/traffic | python3 -m json.tool | head -40   # jira-mock; 8081 bcm-mock, 8000 bmc-farm
```

The web page merges the three at `/api/traffic`; the Traffic tab's analysis reads it. The latency is the time the mock spent between reading the request line and sending the answer, so it measures the mock, not the network. A client that reuses one connection is not charged for idle time between requests. A client that sends a User-Agent shows as its own caller, so you can see which of its timers hits which surface.

## Know what is real and what is not

Slurm is the real software ([ADR 0025](../adr/0025-slurm-is-real-the-rest-is-simulated.md)), so a client sees real command output and real state changes.

| | Real | Simulated or not exercised |
|---|---|---|
| Slurm | `squeue`, `sinfo`, `sacct`, `scontrol` text and flags; node states (`down*`, `mixed`, `drain`); pending reasons (`ReqNodeNotAvail`); `slurmrestd` JWT auth; a frozen node going `down*` by itself after about 30 s | GPUs (count-based GRES), jobs (`sleep`), the health checks' inputs, accounting storage unless the `accounting` part is on |
| Not exercised | | cgroup limits, real GPU binding, resource contention, `gres/gpu` in `AllocTRES` without `slurmdbd` |

Do not report one of the last row as a client defect.

**Things that look like bugs but are not:**

- Slurm GPU counts in a client come from node state (a node with a running job reads `mixed`), not from GRES allocation. The simulator cannot report `gres/gpu` in `AllocTRES` without `slurmdbd`.
- A client's node inventory may refresh more slowly than its jobs list, so a node can look idle for a few seconds after a job starts.
- `sinfo` folds names (`cn[001-004]`); assert on the fold, not on single names.

**Known gaps:** Kubernetes has no real kubelet (no pod logs, no metrics-server); with the Teleport and k8s parts both on it is reachable through `tsh kube`. Teleport has no dynamic labels, session recording or sharing. The fake `cmsh` console is a read-only subset.

## Check the client after the plan changes

When the web page's **Cluster architecture, Apply** rebuilds the workbench under a connected client, check that the client follows:

- new nodes appear, and `ssh <node>` from the login node works;
- removed nodes disappear from Slurm and from the client, without stale entries;
- GPU metrics for new GPU nodes show in Grafana.

Then use a node's **Actions** in the web page (power off, overheat, fan failure, GPU error) and confirm the client sees each through BMC, BCM and the GPU metrics.

## Attach a test report to a bug report

After a scenario has run, write down what it did and what the client called, without screenshots. Play the scenario with the page up: on the page (**▶ Play**), or `./scripts/benchctl scenario run NAME` (it posts its steps to the page when the page answers). Then:

```bash
./scripts/benchctl export test-report last -o test-report.md     # Markdown, to attach
./scripts/benchctl export test-report last --json                # the same data for a script or an agent
```

`RUN` is `last` (the page's last run) or that scenario's name or reference (`SC-02`). The report has the scenario, the result, where it ran from, the start and length, every step with the second after Play it ran, every expectation with its result and time, and for each mock the calls and errors it counted during the run. A last part, **At export**, says what is true when you made the report, not when the run ended: the nodes' container states, the faults and the firing alerts. A part the command cannot read is "not read", never an empty list.

What the report does not hold: request bodies, header values, tokens, hostnames, file paths, a step's raw action (only its sentence) and capture contents. A step or expectation message that holds a path, an address or a password is cleaned (`<path>`, `<address>`, `<redacted>`).

The page keeps the last run in memory only, so it must be up while the scenario plays; after a page restart there is nothing to export. One message says so, with the way out: `nothing to export: the page has no scenario run ...`. It also stops, with the command to wait, when the run is still playing. This is not the config lab's report (`export lab-report`, issue 614). The same report is the MCP tool `report_export`.

**Exercised:** the builder (it gives the page's own report, byte for byte, in a test that runs both), the run state's counters, the command and its messages, the schema and a leak check, all with fakes. **Not exercised:** a real export after a live run against the running stack (`./scripts/verify.sh --unit webui` runs `test_export_test_report_of_a_page_run_and_a_cli_run_holds_the_mocks_counters`, and it is yours to run), and the client's own screens.

## Verify without a GUI

A headless check is faster than driving a window. Connect with any SSH library to `127.0.0.1:2222`, run the client's fixed Slurm command and parse it with the client's own parser. `tests/real/test_login_real.py`, `tests/real/test_gpu_grafana_real.py`, `tests/real/test_bmc_tls_real.py` and `tests/real/test_mocks_real.py` assert the same wire behaviour against the live stack (`WORKBENCH_REAL=1 pytest tests/real/<file> -q`).

For the real window, run the client in its dev mode with a throwaway config directory and a remote-debugging port, create a synthetic profile through its own API, then read screenshots and page text over that port. Use synthetic data only, stop every process you start, and never commit screenshots that show real data.

## Open items after the real Jira run

Results of the run against the real Jira (setup, every number, what was not exercised): [client test report](client-test-report-real-jira.md). Built but not yet run against the client:

- a real slow, 503 and 429 through the fault proxy (SC-28; the client must use `127.0.0.1:8084`);
- tickets and a ticket check on the real Jira in `client-stress-real`, and the token revoke fault (SC-29);
- the client's Jira panel under the real faults, and a script for the client stress run.

## Under the hood

<p align="center"><img src="../assets/art/guide-client-testing-diagram.svg" alt="Bring the cluster up, point the client at it, run a scenario, watch what the client shows, then stop" width="720"></p>

> [!HOOD]
> The client connects as it would to a production site: SSH to the login node, Slurm commands, Grafana GPU metrics, Jira search and Redfish BMCs. Each endpoint has a fixed address, a credential file under `compose/generated/secrets/` and a status in the table above. The governor can refuse a start at L2 and above (GV-9); the tool says so, and a test or script must not report that as a defect in the client (NF-6).

Source: [ADR 0018](../adr/0018-fixed-addressing-plan.md), SPEC G5, NF-6, NF-8

## Tips

> [!TIP]
> Run `benchctl scenario run NAME --dry-run` first: it prints the steps and touches nothing.

## If it goes wrong

| Symptom | Try |
|---|---|
| Port 2222 refuses | the checkout is probably on a branch without the login node; that is not a client bug |
| A start is REFUSED | the governor is protecting the host; free memory |

## Learn more

- [Client setup by component](client-setup-by-component.md)
- [SSH connection testing](ssh-connection-testing.md)
- [Scenarios](../manual/06-scenarios.md)
