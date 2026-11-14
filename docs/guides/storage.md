# Storage: declared mounts and a shared scratch

No NFS, Lustre or DDN appliance runs here: they need root and kernel modules (SPEC C-11, [ADR 0078](../adr/0078-storage-tiers-and-a-real-object-store.md)).
What you get instead:

| | What it is | Where users meet it |
|---|---|---|
| **Declared mounts** | a path, the kind of file system it stands for (`nfs`, `lustre`, `ddn`, `local`), a size, how full it is, its inodes, a per-user quota | `df`, `df -i`, `df -hT`, `lfs df`, `lfs quota -u USER` on every Slurm node and login node; the `node_filesystem_*` series |
| **A shared path** | one real directory on every Slurm node and login node: a Docker volume, or a directory of yours | `/scratch/USER` (and `/home` when you share it): write a file on one node, read it on another; a job's `-o` file appears on the login node |

Two mounts exist with no setup: `/home` (NFS, 20 TB, 35 % full) and `/scratch` (Lustre, 500 TB, 40 % full, **shared**).

## Use it

```bash
./scripts/benchctl up
ssh alice@127.0.0.1 -p 2222            # the login node (see the manual, chapter 4)
df -hT /scratch /home                  # the declared size, used, free and type
df -i /scratch                         # inodes
lfs df -h /scratch                     # MDT, OSTs and a summary
lfs quota -u alice -h /scratch         # alice's usage against her quota
echo dataset > /scratch/alice/shard-000
sbatch -o /scratch/alice/job-%j.out --wrap 'hostname'   # the output file is on the login node too
```

`df` of a path that is not declared (`/`, `/tmp`) is the real `df`; `df` with no path lists the real rows and the declared ones together.
The numbers are declared, not measured: **what can really be written to a shared mount is the host's free disk space**, whatever `df` says.

## Declare your own

In `sim.yaml` (a `storage:` section replaces the two built-in mounts; every key but `path` is optional):

```yaml
storage:
  mounts:
    - {path: /home,    kind: nfs,    source: "nas01:/export/home", size_tb: 20,  used_pct: 35, inodes: 2000000000, inodes_used_pct: 30, quota: {gb: 100, files: 1000000}}
    - {path: /scratch, kind: lustre, source: "mgs01@tcp:/scratch", size_tb: 500, used_pct: 40, inodes: 4000000000, inodes_used_pct: 25, shared: true, quota: {gb: 20000, files: 50000000}}
    - {path: /datasets, kind: ddn,   size_tb: 2000, shared: true, host_path: /mnt/bigdisk/datasets}   # users' training data, on a disk you name
```

- `shared: true` makes the path one directory on every Slurm and login node, with a directory per simulated user (mode 750). Without `host_path` it is the Docker volume `workbench_shared-NAME`: it survives `benchctl down`; `benchctl remove storage-state` deletes it. With `host_path` the directory is yours: create it first, it is never deleted.
- A shared `/home` holds the users' SSH keys and kubeconfigs (the login node writes them at start); it is off by default.
- Wrong values are refused with one message each (`benchctl compose` and `benchctl up`).

## Scenarios

`benchctl scenario run disk-full` (SC-41: `/scratch` at 99 % of bytes, 90 % of inodes), `quota-exceeded` (SC-42: alice over her quota, `lfs quota` shows `*` and a grace time such as `6d23h59m59s`) and `stale-mount` (SC-43: `cn002` loses `/scratch`: `df` fails with the file system's own message, its series disappear and `node_filesystem_device_error` is 1). Each resets itself after five minutes. A step is
`{target: gpu-exporter, do: {nodes: [cn001], storage: {mount: /scratch, used_pct: 99}}}`; keys: `used_pct`, `inodes_used_pct`, `stale: true|false`, `user` with `used_gb`, and `reset: true`. The expectation is `storage.mount: {node, mount, min_used_pct, min_inodes_used_pct, quota_exceeded: USER, stale}`.

## The object store (optional)

`benchctl up --with objectstore` adds one Garage container (S3 API, localhost port 3900, 384 MB limit, never in `--addons` or `all`). Every Slurm and login node can use it with no setup:

```bash
benchctl ssh alice@login
s5cmd cp results.tar s3://$WORKBENCH_S3_BUCKET/results.tar     # on one node
s5cmd ls s3://$WORKBENCH_S3_BUCKET/                            # on another
```

The endpoint, region (`garage`) and key pair come from `/run/workbench/objectstore/s3.env`, which a login shell loads. The key pair is generated once in `compose/generated/secrets/objectstore` and kept across restarts, so stored data stays readable. Prometheus scrapes the store (`up{job="objectstore"}`, `garage_local_disk_avail`). `benchctl down` keeps the data in `compose/generated/objectstore/data`.

## What is and is not simulated

- Simulated by declaration: sizes, fill levels, inodes, quotas, the Lustre `df`/`quota` layouts (one MDT and four OSTs), a stale handle. The output follows GNU `df` and `lfs` in layout; it is not byte-identical, and only `lfs df` and `lfs quota -u` exist.
- Real: the shared directory and its permissions. Its performance is a local disk's, not a parallel file system's.
- Not possible: a kernel NFS or Lustre mount, or a FUSE mount, inside a node (they need `CAP_SYS_ADMIN`; containers stay unprivileged).
- The metrics are per node and per mount: `node_filesystem_size_bytes`, `_free_bytes`, `_avail_bytes`, `_files`, `_files_free`, `_readonly`, `_device_error`, with `mountpoint`, `device` and `fstype`.
