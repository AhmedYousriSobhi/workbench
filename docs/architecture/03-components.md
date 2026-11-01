# Component decisions

[Architecture index](README.md) · [Project overview](../../README.md)

## 4. Component decisions (what, why, alternatives)

### 4.1 Slurm: containerized, not VMs
- **Real daemons, not a mock** (ADR 0025): the scheduler, its node states, command output and `slurmrestd` auth are Slurm's own; GPUs, cgroups and jobs are simulated. The ADR lists what is real, what is not and the cost.
- Run `slurmctld` + `slurmrestd` in core; `slurmdbd` + MariaDB only in profiles that test accounting/QOS.
- One container per fake compute node, all from **one shared base image** (shared file-backed pages). Community projects such as `giovtorres/slurm-docker-cluster` already use this pattern (controller, DB, and `c1`/`c2` `slurmd` containers under Docker Compose); use it as a starting point rather than writing from scratch.
- Container-friendly `slurm.conf` starting points (validate on your Slurm version): `ProctrackType=proctrack/linuxproc`, `TaskPlugin=task/none`, fake `RealMemory`, and GPUs declared as GRES by count (`Name=gpu Type=h200 Count=8`, no device files) so GPU partitions behave like a DGX/HGX fleet.
- Jobs are synthetic (`sleep`, `stress-ng` with capped memory), never real training.
- Optional later optimization: build Slurm with multiple-slurmd support to host many logical nodes in one container if per-container overhead matters.

### 4.2 Kubernetes: one real mini-cluster + KWOK for scale
- Replace the multi-VM Vagrant K8s with **k3d or kind**: 1 control-plane + 1 real worker (for real pods, real metrics).
- Add **KWOK (Kubernetes WithOut Kubelet)** for scale and faults: it simulates node and pod lifecycles against the real API server, with a very small footprint (the project advertises 1k nodes / 100k pods on a laptop). Fake GPU nodes advertise `nvidia.com/gpu` capacity; flipping a node to `NotReady` is a one-line fault.
- Constraint: kind/k3d create their node containers themselves, so don't assume you can set a cgroup parent for them. The governor addresses them by container name/label through the Docker API.

### 4.3 BMC: Redfish farm
- Build **`bmc-farm`**: a single process serving N virtual Redfish endpoints (per-port or per-virtual-host), each with inventory, power state, thermals, fans, sensors and an event log. Faults are injected via a control API (`/sim/inject`).
- Reference implementations: DMTF Redfish mockups for static trees; **`sushy-tools`** (OpenStack) offers a static responder and a *virtual* Redfish BMC backed by libvirt. Use sushy-tools' libvirt-backed emulator **only for the one fidelity VM** so power on/off really controls a VM.
- Add an IPMI facade later only if the app needs it.

### 4.4 BCM (NVIDIA Base Command Manager): API-compatible mock
- A real BCM head node is licensed, heavy software; don't try to run one here.
- Build **`bcm-mock`** implementing only the surface the app calls. NVIDIA documents the CMDaemon **REST API** (`/rest/v1/monitoring/...` entities, measurables, usage) and the **`pythoncm`** Python API (nodes, power status, monitoring data); mirror whichever the app consumes, with recorded real responses as fixtures where you have them.
- Keep the mock data-driven (YAML fixtures + the same `sim.yaml` node list) so BMC, BCM, Slurm and Prometheus always agree on node names.

### 4.5 Jira: three modes behind one switch
Note: Jira Cloud cannot be self-hosted, and Atlassian ended Jira Server support in February 2024; self-managed Jira today means Data Center (licensed, JVM-heavy). So decide per mode:

| Mode | RAM | When |
|---|---|---|
| `mock` (default) | ~50-100 MB | Daily dev, CI, scenarios. a small stdlib server (built; WireMock was the alternative) implementing the Jira REST endpoints the app uses (issue create/transition/comment, search/JQL subset, webhooks), with failure modes (429, 503, slow). |
| `cloud` | 0 local | Contract testing against a real **Jira Cloud free site** (small-team free tier; check current limits). Real auth, real rate limits. |
| `dc` | ~3 GB+ | Only the `jira-real` profile, which suspends K8s + fidelity VM to pay for it. Keep heap at the recommended size; don't starve it. |

Put **Toxiproxy** in front of the Jira mode only (an HTTP path, where it is cheap) to inject latency/outages. Do **not** put it in the Slurm RPC path; use `tc netem` there (section 4.9).

### 4.6 Observability
- Prometheus (retention about 2d, 15-30s scrape, capped cardinality) + Grafana + Alertmanager. Dashboards provisioned from files so they're reproducible.
- **`gpu-exporter-farm`**: fake DCGM-style metrics (utilization, memory, temp, power, XID/ECC counters) for every simulated GPU node, controllable by scenarios.
- The sim's own health (governor metrics) is exposed separately so it survives if Prometheus is stopped.

### 4.7 Workload and people simulation
- **`user-sim`**: personas (ML trainer, MPI researcher, array-job student, interactive user) with accounts/QOS, Poisson arrivals, a daily rhythm, and failure behavior (bad scripts, OOM jobs, cancellations). Optional: replay a Standard Workload Format trace.
- **`ticket-bot`**: fake support engineers that pick up tickets the app creates, comment, transition and resolve them on realistic delays, including "customer" tickets the app should triage.
- Submit through `slurmrestd` and the CLI so both integration paths of the app are exercised.

### 4.8 Fidelity VM (the single real VM)
- One Vagrant/libvirt Ubuntu VM, **on demand only**, for what containers can't prove: real kernel counters, systemd units, sshd, real `node_exporter`, power control via sushy-tools, and the app's Linux agent install path.
- Prefer **libvirt/KVM** (`vagrant-libvirt`) over VirtualBox: KVM supports memory ballooning and KSM page merging, and `virsh managedsave` can fully release its RAM.
- Hard-cap at 2 GB.

### 4.9 Storage, network and time simulation
- **Fake shared filesystems.** No NFS/Lustre servers. Emit static `node_exporter` textfile metrics (`node_filesystem_size_bytes`, `node_filesystem_free_bytes`) for dummy mounts such as `/mnt/lustre`; scenarios like `disk-full` just change the numbers.
- **tmpfs for ephemeral state.** MariaDB (Slurm accounting) and the Prometheus TSDB live on a capped tmpfs (about 200 MB each). State need not survive a host reboot, and this avoids IO saturation next to 30+ containers.
- **Network chaos with `tc netem`.** Latency, loss and partitions are applied on the Docker bridge networks between `core` and `mocks`/`compute`; no proxy process, no extra RAM.
- **Time dilation.** `time_dilation` in `sim.yaml` scales synthetic job durations and arrival rates (inversely) in `user-sim` and `scenario-engine`, so a 24 h queue pattern can run in minutes.
