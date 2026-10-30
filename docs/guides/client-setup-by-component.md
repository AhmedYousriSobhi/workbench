# Setting up the monitoring client, one component at a time

[client-testing.md](client-testing.md) lists every endpoint; the [manual](../manual/README.md) shows the tool from
scratch. This page is for the other case: the cluster is running and you want to connect **one** part of your client
(SSH, Slurm, Grafana, storage, Jira, BCM, BMC) without reading the rest. Each section says what to start, where to find
each value, what to type, what you should see, and the usual failure.

Every section uses synthetic data on `127.0.0.1`. Field names are the client's own labels, as in its cluster form.

## 0. Start the cluster once

```bash
./scripts/benchctl up --profile min      # REFUSED means the host is short of memory: free some and run it again
./scripts/benchctl status                # governor level L0 or L1 is fine
```

`up` prints the values you need and writes the secrets into `compose/generated/secrets/` (never copy them into tracked
files). **Every `up` creates new Grafana tokens**; after a restart, paste the new one (section 3).
When you are done: `./scripts/benchctl down`.

## 1. SSH and the terminal

| Client field | Value |
|---|---|
| Host / Port | `127.0.0.1` / `2222` |
| Username | `alice` (or `bob`) |
| Auth method | Private key |
| Private key | a file under `~/.ssh` (the client refuses other places) |

Get the key where the client can read it:

```bash
./scripts/benchctl ssh-key install       # copies it to ~/.ssh/opti-sim_ed25519 (mode 600); give the client that path
./scripts/benchctl ssh-key remove        # when you are done
```

**You should see:** the first connection asks you to trust the host key (accept it; it stays the same across restarts),
then a bash prompt `alice@login:~$`. Press Tab after `sq`: it completes to `squeue`.

**If it fails:** `connection refused` on port 2222 means the cluster is down or on a branch without the login node. A
"host key changed" warning is expected only after the `ssh-host-key-changed` scenario. Step-by-step with screenshots:
[ssh-connection-testing.md](ssh-connection-testing.md).

## 2. Slurm (jobs and node states)

Needs section 1 working. In the cluster's scheduler settings:

| Client field | Value |
|---|---|
| Run Slurm commands on a different node | leave blank (the login node has the Slurm tools), or `cn001` to test the hop |

Give it something to show, in the terminal:

```bash
sbatch --wrap="sleep 300" -p cpu                 # a CPU job
sbatch --wrap="sleep 300" -p gpu --gres=gpu:2    # a GPU job
squeue --me
```

**You should see:** partitions `cpu` (4 nodes) and `gpu` (2 nodes), and your jobs as running. To fill the node-issues
view: `docker exec hpcsim-slurmctld-1 scontrol update nodename=cn004 state=drain reason="test"`, and `state=resume`
afterwards.

**If it fails:** an empty job list usually means no job is running yet. `sacct` history needs an accounting profile
(not in `min`).

## 3. Grafana (status and GPU panels)

| Client field | Value | Where it comes from |
|---|---|---|
| Grafana base URL | `http://127.0.0.1:3000` | fixed |
| Dashboard UIDs | `hpc-overview` | the provisioned dashboard "HPC Cluster Overview (opti-sim)" |
| Service account API token | the Viewer token | `cat compose/generated/secrets/grafana_viewer_token` |
| GPU datasource UID (advanced) | `prometheus` | fixed |
| Node label (advanced) | `Hostname` | the DCGM label on the GPU series |

Paste the token with no spaces or newline around it, then **Save**.

**You should see:** "Grafana reachable", then the dashboard card with its panels; pick **GPU utilization** to see two
lines, `gpu001` and `gpu002`.

**If it fails:**

- **HTTP 401 next to "Grafana reachable"**: the token is wrong or stale. The health check needs no login, so
  "reachable" does not prove the token. Copy the current file again (it changes on every `up`).
- **Panel empty for the first minute after `up`**: the GPU exporter is still starting. Wait and refresh.
- The GPU lines are synthetic and do not follow your Slurm jobs yet (issue #83).

## 4. Storage

In the cluster's storage settings, paths such as `~` work over SSH (it runs `df` as `alice`, and reads the container's filesystem). Lustre-style quota
commands are not simulated yet (roadmap, issue #69). The dashboard's `/mnt/lustre free` panel is static data.

## 5. Jira

| Client field | Value |
|---|---|
| Jira type | Jira Data Center (PAT) |
| Jira base URL | `http://127.0.0.1:8080` (plain HTTP is accepted for localhost) |
| API token | any text (the mock treats a Bearer token as user `alice`) |
| Default project key | `OPS` (the mock's project; `curl -s 127.0.0.1:8080/rest/api/2/project` lists it) |

**You should see:** the ticket list. To test the client's error path, break Jira on purpose:

```bash
curl -s -X POST 127.0.0.1:8080/sim/failure -d '{"mode":"429","count":3}'     # or 401, 503, slow
```

## 6. Base Command Manager (BCM)

The login node is the head node, with a read-only `cmsh`. In the cluster's **Base Command Manager** section:

| Client field | Value |
|---|---|
| Host | the same `127.0.0.1`, port `2222`, over the SSH connection from section 1 |

Press **Test BCM Connection** (save the cluster first). You can check by hand what it runs:
`ssh -p 2222 alice@127.0.0.1 'cmsh -c "device; list"'` (it lists the head node `login` and the nodes with their status). Only reading works; the interactive console is not available.

## 7. BMC (Redfish)

| Value | |
|---|---|
| Address | `https://127.0.0.1:8443`, one node per `Host` header `bmc-<node>` (for example `bmc-gpu001`); through the login node it is `bmc-gpu001:8443` |
| User / password | `admin` / the password after the colon in `compose/generated/secrets/bmc_credentials` (the file reads `admin:<password>`) |
| Certificate | self-signed; the client must be told to trust or pin it |

Check by hand: `curl -sk -H "Host: bmc-gpu001" -u "admin:<password>" https://127.0.0.1:8443/redfish/v1/` (it answers "gpu001 service root"). Break something with
`./scripts/benchctl scenario run fan-failure`.

## 8. Try a fault

With the client open on the cluster, play one scenario and compare what the client shows with what the scenario did:

```bash
./scripts/benchctl scenario list
./scripts/benchctl scenario run node-down --speed 4
```

More in [scenarios and faults](../manual/06-scenarios.md).
