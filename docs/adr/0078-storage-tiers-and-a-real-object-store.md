# ADR 0078: storage simulation in three tiers, the third a real object store

Status: accepted by the owner on 2026-11-14 (issue 461, epic 557), C-11 text approved. **Tiers 1 and 2 are built (the live run is pending); tier 3 (the object store) is not built yet.**

## Context
SPEC C-11 says "No NFS/Lustre servers. Static `node_exporter` textfile metrics for fake mounts." Today the only storage signal is two random-per-scrape series for one mount inside the GPU exporter (`node_filesystem_size_bytes`, `node_filesystem_free_bytes`), so a client cannot see a full disk, a quota hit or a stale mount, and a job's output file is not visible from the login node (each container has its own `/home`).

## Decision (tiers 1 and 2: built)
1. **Declared storage.** A `storage:` section in `sim.yaml` (mounts such as `/home`, `/scratch`; a kind, size, fill level, inodes and per-user quotas for each). **Built-in defaults apply without it** (`/home` NFS 20 TB and `/scratch` Lustre 500 TB, the latter shared), because the exporter's random series had to be replaced by something. From it: filesystem metrics per node and mount instead of the random ones, `df` and `lfs quota`-style shims on the login node (the same pattern as the fake `cmsh`), and scenarios `disk-full`, `quota-exceeded` and `stale-mount` with expectations on the reaction.
2. **One shared POSIX volume.** One Docker named volume mounted at `/home` and `/scratch` on the login node and every slurmd node, so a job's output file appears on the login node as on a real cluster. No privilege, no new process; the cost is page cache, which the kernel reclaims. The login image seeds each user's home (SSH key, kubeconfigs) into the volume at start, because a mount hides the image's own `/home`.

## Decision, tier 3 (needs the owner's yes)
3. **A real storage service.** One S3-compatible object store container (MinIO or SeaweedFS; about 100 to 150 MB, class P6, memory-limited, unprivileged) standing for the site's object or archive tier, so a client can meet a real storage API: bucket and object calls, its own `/metrics`, real failures (stop it, fill it, delete a bucket). It is **not** NFS or Lustre and does not serve the POSIX mounts of tiers 1 and 2; those stay a volume and shims. `benchctl up --with objectstore`, off by default and never in `--addons`/`all`.

### SPEC C-11, proposed text
> No NFS or Lustre servers (a real one needs privilege and kernel modules). Storage is declared in `sim.yaml` `storage:` and shown as `node_exporter`-style metrics, `df`/`lfs quota` shims and scenarios; `/home` and `/scratch` are one shared Docker volume, so job output is visible from the login node. Optional part `objectstore` (off by default, P6, about 150 MB): one S3-compatible object store container with its own metrics. MariaDB (100 MB) and the Prometheus TSDB (200 MB, `retention.size` 150 MB) stay on capped tmpfs.

CLAUDE.md's hard rule ("Do not add real NFS/Lustre") stays as it is; only its pointer to C-11 gains the object store.

## Owner answers (2026-11-14)
1. Tier 3 and the C-11 text: **approved**.
2. MinIO or SeaweedFS: **survey first** (below); the owner then asked me to do the research myself and to "do the best recommended"; the choice below follows from it (Garage) and is open to correction.
3. What the client reads: **the disk space and the inodes of a path**, and **the nodes must be able to reach the storage**.

### What that means for the tiers
- Space and inodes of a path are POSIX facts (`df`, `df -i`, `lfs df -i`, `node_filesystem_size_bytes`, `node_filesystem_files`, `node_filesystem_files_free`), not object-store facts. An S3 service has buckets, not mounts or inodes, so **tier 3 cannot answer what the client reads; tiers 1 and 2 do.** Tier 1 therefore must give, for each declared path, size, used, free **and inodes** (total, used, free) in the node metrics and in `df`, `df -i` and `lfs df` / `lfs quota` output shaped like the real system's (an NFS export `server:/export`, a Lustre `mgs@o2ib:/lustre`, a vendor appliance), so a client that parses those lines sees the usual shape.
- "Reach the storage from the nodes": tier 2's shared volume is the POSIX path on every Slurm node and the login node. Tier 3 is reached from the nodes over HTTP, with `curl` or an S3 command-line client added to `node-base`'s single apt layer. **A kernel NFS or Lustre mount, or a FUSE mount of the object store, needs `CAP_SYS_ADMIN` or `/dev/fuse`; containers stay unprivileged (CLAUDE.md), so none of those is possible.** Say so if the client needs a real mount; that is a different decision.

### The shared dataset path (owner, 2026-11-14)
Users keep large LLM training datasets on one path shared by all nodes. That is tier 2, and it is the main storage feature, not a side effect: `/scratch` (and `/home`) are one volume on the login node and every Slurm node. Consequences to settle when building it: the volume's size is the host disk's free space (a `storage:` size only changes what `df` and the metrics report, never what can be written, so a user writing a real 200 GB dataset fills the host); a per-volume host path (a bind mount to a directory the owner names, for example on a larger disk) is offered next to the named volume; the governor's reclaim path does not touch it. A dataset directory for each user is created at start.

### Other storage systems considered, for a shared path
| System | What it gives | Usable unprivileged in this project? |
|---|---|---|
| Docker volume or bind mount (tier 2) | a real shared POSIX path, real `df` and inodes of the host file system | **yes**, the choice |
| NFS (kernel server, or user-space Ganesha) | the shape of most sites' home and scratch | no: the client mount needs `CAP_SYS_ADMIN` |
| Lustre, BeeGFS, CephFS, GPFS | the shape of HPC and DDN-class scratch | no: kernel modules and root; ruled out by C-11 and CLAUDE.md |
| JuiceFS, SeaweedFS mount, rclone mount, GlusterFS | POSIX over an object or distributed store | no: FUSE needs `/dev/fuse` and `CAP_SYS_ADMIN` |
| S3 store (tier 3) | what dataset loaders for training commonly read (WebDataset, Hugging Face, `s5cmd`, `rclone copy`) | **yes**, over HTTP, not as a path |

So the realistic pair is a shared volume for the POSIX path and an S3 store for the object path; the others need a privilege the project does not grant. The *look* of NFS, Lustre or DDN comes from the shims in tier 1 (`df` mount source, `lfs df`, `lfs quota`), not from running them.

### Survey of object stores (2026-11-14, read from the vendors' pages and issue trackers; memory figures are third-party measurements)
| | MinIO | SeaweedFS | RustFS | Garage |
|---|---|---|---|---|
| State | community repository archived in 2026, no official images; community forks (`pgsty/minio`) | active | 1.0 GA announced for 2026-09-16; open issues about memory that grows and is not released (1.0.3, 1.0.4) | active, tags v2.2.0 to v2.5.0 |
| Licence | AGPLv3 | Apache 2.0 | Apache 2.0 | AGPLv3 |
| Image | none official | 96 MB compressed (4.23, with a second Rust binary) | about 110 to 158 MB | about 10 to 20 MB compressed |
| Memory | n/a | about 141 MB for `weed server -s3` on 4.41 (one informal test); 250 to 360 MB reported for the filer on 4.18+ against 80 to 90 MB on 4.17 | about 250 MB idle, 650 MB with 1.5 GB of data on 1.0.3, 1.5 to 2.6 GiB after load on 1.0.4 | tens of MB (22.6 MiB idle on v2.4.1, 30 to 60 MB in another comparison); its own guide asks for 512 MB for production |
| Single node | n/a | one process: master, volume, filer, S3 | one pod, one volume | `--single-node` (v2.3.0 and later) lays itself out; `--default-bucket` and `GARAGE_DEFAULT_ACCESS_KEY`/`_SECRET_KEY` make the bucket and key at start |
| Metrics | `-metricsPort` per server | not confirmed | `GET /metrics` on the admin port (3903), guarded by `metrics_token`: `garage_local_disk_avail`/`_total` (data and metadata volumes), `cluster_healthy`, `cluster_available`, `cluster_connected_nodes`, `cluster_storage_nodes_ok`, `block_bytes_read`/`_written` |
| Extras | | filer path view, WebDAV, FUSE (unusable here) | | S3 only |

**Decision: Garage**, pinned to `dxflrs/garage:v2.5.0` (a newer tag is an ADR addendum). It is the smallest, has a one-flag single-node start with the bucket and key created at boot (no layout or key commands to script), exposes Prometheus metrics with space and health, and is not showing the memory growth that RustFS and SeaweedFS have open reports about. SeaweedFS is the second choice if a path-shaped view is ever wanted; RustFS is too young for a service that must stay at a few hundred MB; MinIO has nothing official to pull.

Facts about how it runs: config at `/etc/garage.toml` (`metadata_dir`, `data_dir`, `db_engine = "sqlite"`, `replication_factor = 1`, `rpc_bind_addr`, `rpc_secret`, `[s3_api]` on 3900 with `s3_region = "garage"`, `[admin]` on 3903 with `admin_token` and `metrics_token`); ports 3900 S3, 3901 RPC, 3902 web, 3903 admin; no volumes by default, so data and metadata need a volume or a tmpfs. The image's user is not documented on that page and must be checked before the service is declared unprivileged.

**AGPLv3:** the repository does not redistribute Garage; `up` pulls the upstream image at run time, as it does the Kubernetes images. The owner has been told and decides for a public repository.

**What the nodes get:** the endpoint `http://objectstore:3900`, the region `garage` and a generated key pair in a read-only secret mounted on every node and login node, plus a small S3 client (to be chosen when tier 3 is built: a pinned static binary with its SHA256, never a package pulled at start).

### What the research says about the formats the shims print (read, not captured from a site)
- `lfs df`: a header `UUID ... Use% Mounted on`, one row per target named `FSNAME-OST000c_UUID` (the index is hexadecimal) ending in `/mount[OST:12]` (UTK's page); a `filesystem_summary:` row and MDT rows are not shown by any page found, so they come from knowledge of the tool.
- `lfs quota -u bob /mnt/testfs`: `Disk quotas for user bob (uid 500):`, columns `Filesystem kbytes quota limit grace files quota limit grace`, an asterisk on a value over its limit and a grace such as `6d23h56m44s` (the Lustre manual; newer releases print `usr` and `used`).
- A Lustre client's `df` source is `MGSNID@tcp:/fsname` or `10.51.22.11@o2ib:10.51.22.10@o2ib:/lustre/home`; an unreachable mount fails `df` with `Input/output error` or, after an eviction, `Cannot send after transport endpoint shutdown (-108)`; a stale NFS handle shows `Stale file handle`.
- `node_exporter`: `node_filesystem_avail_bytes` is the space for non-root users and `node_filesystem_files_free` the free inodes. Lustre's own counters: `filesfree`/`filestotal` and `kbytesfree`/`kbytestotal` per MDT and OST; the exporters are `lustre_exporter` (HPE, inactive since 2022), its GSI fork, and `lustrefs-exporter` (Whamcloud, Rust).
- AI clusters: NVIDIA's DGX guides keep datasets and checkpoints on a shared parallel file system from a partner (DDN's AI400X2 for BasePOD) with node-local NVMe absorbing frequent checkpoints; dataset loaders such as Mosaic Streaming read shards (about 100 MB to 1 GB) from S3-compatible stores and keep a bounded local cache; Pyxis/Enroot mount shared paths with `--container-mounts host:container`. This is the reason for the pair this ADR builds: a shared path and an S3 store.

## Not verified
All of it. The shared-volume seeding of user homes and the effect on SSH login are the main risks for tier 2; the image size and idle memory are the main unknowns for tier 3.

## Consequences if accepted
- `sim.yaml` gains `storage:` (ignored unless present); the GPU exporter's random filesystem series go away (M4).
- The login and node containers gain one volume; `benchctl down` keeps it, `benchctl remove storage-state` clears it.
- One optional part with a memory budget and priority class, as every component declares.

## What was built (tiers 1 and 2, issue 461)
`benchctl/storage.py` (validation, defaults, volumes), the declared mounts in `inventory.json`, per-node `node_filesystem_*` series (size, free, avail, files, files_free, readonly, device_error) from the exporter farm, `GET /sim/storage` there, `images/node-base/fakefs.py` as `df` and `lfs`, one shared Docker volume (or a `host_path` bind) on every Slurm and login node with a directory per user, scenarios SC-41 to SC-43, `benchctl remove storage-state`, [the guide](../guides/storage.md). `/home` is declared but not shared by default: sharing it puts the users' SSH keys and kubeconfigs in a volume; `shared: true` turns it on.

## Not verified yet
`./scripts/verify.sh --unit storage`: that the volume's directories are writable by the simulated users on every node, that a Slurm job's output lands on the login node, that the shims run in the nodes' Python and reach the exporter by name, and that a stale `df` fails as written. The Lustre and NFS output shapes were written from memory of the real tools, not captured from a site; compare with a real `lfs df` before a client parses them. The quota numbers are invented defaults.
