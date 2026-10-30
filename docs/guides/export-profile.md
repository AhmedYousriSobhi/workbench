# Export the workbench as a client profile

Typing a cluster into the monitoring client by hand takes a dozen fields. The client can import a **profile file**
instead; `benchctl export profile` writes one that matches what the workbench contains.

## Steps

```bash
./scripts/benchctl up --profile min              # or: benchctl start
./scripts/benchctl export profile                # writes bench-profile.json in the current folder
```

Add `--bmc` for one BMC target per node, `--user bob` for another SSH user, `--name lab` for another cluster name,
`-o -` to print instead of write, `--profile std` to export a profile other than the one running.

Then in the client: **import a profile**, choose `bench-profile.json`. The cluster appears with SSH, Slurm, Grafana,
Jira and storage already filled in. A file never holds secrets, so set these three by hand (the command prints them):

| Missing on purpose | Do this |
|---|---|
| SSH key | `./scripts/benchctl ssh-key install`, then pick `~/.ssh/opti-sim_ed25519` in the cluster's SSH section |
| Grafana token | `cat compose/generated/secrets/grafana_viewer_token`, paste it, save (it is new after every `up`) |
| BMC password (with `--bmc`) | the part after `admin:` in `compose/generated/secrets/bmc_credentials` |

## What is in the file

Only parts the chosen profile builds: SSH (`127.0.0.1:2222`, user `alice`, private-key auth), Slurm (partitions from
`sim.yaml`), Grafana (`http://127.0.0.1:3000`, dashboard `hpc-overview`, GPU datasource `prometheus`, label
`Hostname`), Jira (`http://127.0.0.1:8080`, project `OPS`), storage (`~`) and, with `--bmc`, a target per node
(`127.0.0.1:8443`, user `admin`, the node name). It has no password, token or key path. Kubernetes and BCM are not
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
- **The client says the file is not a profile or is version N**: the ID or version in `.git/export-format` does not
  match the client; copy them again from an export of the client.
- **Import works but Grafana shows 401**: the token expired when you ran `up` again; paste the current one.
