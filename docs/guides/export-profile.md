# Export the workbench as a client profile

Typing a cluster into the monitoring client by hand takes a dozen fields. The client can import a **profile file**
instead; `benchctl export profile` writes one that matches what the workbench contains.

## Steps

```bash
./scripts/benchctl up --profile min              # or: benchctl start
./scripts/benchctl export profile                # writes bench-profile.json in the current folder
```

The file describes the cluster **running now** (read from Docker): a part or node that is stopped is left out. With nothing running it lists the configured cluster and says so; `--configured` forces that. Add `--bmc` for one BMC target per node that has a BMC (each with its own address `10.148.x.y:443`; `--bmc-via name` writes `bmc-<node>` instead), `--user bob` for another SSH user, `--name lab` for another cluster name,
`-o -` to print instead of write, `--profile std` to export a profile other than the one running. With a workbench built ([choose the workbench](choose-workbench.md)) the file lists exactly its nodes and parts.

Then in the client: **import a profile**, choose `bench-profile.json`. The cluster appears with SSH, Slurm, Grafana,
Jira and storage already filled in. A file never holds secrets, so set these three by hand (the command prints them):

| Missing on purpose | Do this |
|---|---|
| SSH key | `./scripts/benchctl ssh-key install`, then pick `~/.ssh/workbench_ed25519` in the cluster's SSH section |
| Grafana token | `cat compose/generated/secrets/grafana_viewer_token`, paste it, save (it is new after every `up`) |
| Jira | nothing: the mock accepts any token or password |
| BMC password (with `--bmc`) | the part after `admin:` in `compose/generated/secrets/bmc_credentials` |

### From the page

Open **☰ Menu → ⇩ Export** in the top strip, tick *One BMC target per node* if you want it, and **Download**. The page has no Docker access, so it lists the cluster on disk (the workbench last built, else the profile `up` started) instead of probing containers. If the format ID below is not set it says so in one line.

## What is in the file

Only parts the chosen profile builds: one **cluster entry per login node**, each with its own way in (`login` direct SSH: `10.141.255.254:22`, user `alice`, private-key auth; a Teleport login: a `teleport` block with the proxy `10.141.255.30:3080`, the DNS name `teleport.workbench.test`, the node and the user; the first entry is a key login and carries Slurm, Grafana and Jira), Slurm (partitions from
`sim.yaml`), Grafana (`http://10.141.255.21:3000`, dashboard `hpc-overview`, GPU datasource `prometheus`, label
`Hostname`), Jira (`http://10.141.255.11:8080`, project `OPS`), storage (`~`) and, with `--bmc`, a target per node
(its own `10.148.x.y:443`, user `admin`). `--loopback` writes `127.0.0.1` and the published ports instead (all BMCs then share `127.0.0.1:8443`) (a client that cannot route to the cluster network, or that insists on HTTPS for non-local Jira). It has no password, token or key path; the Teleport identity file is never in it (`compose/generated/secrets/teleport/identity/<user>`: set it in the client). The `teleport` block uses the workbench's own field names (`proxy`, `dnsName`, `clusterName`, `insecure`, `node`, `username`); the client may name them differently, and the export says so in a warning. Kubernetes and BCM are not
exported: Kubernetes is not built, and BCM is configured in the client over the SSH connection
([by component](client-setup-by-component.md)).

## One-time setting: the client's file format ID

The client's file starts with a format identifier and version that name the client. This repository never writes them,
so give them once on your machine; the check is local and never committed:

```bash
echo 'FORMAT_ID:1' > .git/export-format      # ID or ID:VERSION, taken from the client's own export
```

or pass `--format ID[:VERSION]`, or set `BENCH_EXPORT_FORMAT`. The easiest way to get the value: export any cluster from
the client once and copy the first two fields of its file.

## If it fails

- **`cannot export: the profile format identifier is not set`**: do the one-time setting above.
- **`no login node`**: the profile has no `access` group; use `min`, `std` or `full`.
- **A Teleport-only login is in the file, but the client cannot import it**: the importer does not know the `teleport` block's names. Type the values from the **Connections** tab by hand and tell us the client's names, so the export can match them.
- **The client says the file is not a profile or is version N**: the ID or version in `.git/export-format` does not
  match the client; copy them again from an export of the client.
- **Import works but Grafana shows 401**: the token expired when you ran `up` again; paste the current one.
