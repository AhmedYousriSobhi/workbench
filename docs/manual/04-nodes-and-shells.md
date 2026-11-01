# 4. Nodes and shells

**There is no `ssh` into a simulated node.** The nodes are unprivileged containers without an SSH server. You open a shell
with `benchctl exec` (a thin wrapper over `docker exec`). The only machine with a real `sshd` is the optional [fidelity VM](#the-fidelity-vm).

## Find the nodes

Node names come from [sim.yaml](../../sim.yaml): compute nodes `cn001`, `cn002`, ... and GPU nodes `gpu001`, `gpu002`, ...
Each container is named `workbench-<name>-1`.

```bash
./scripts/benchctl get nodes                                     # the nodes and their state (add -o json for scripts)
./scripts/benchctl get services                                  # every container of the stack
docker ps --format '{{.Names}}' --filter label=workbench.class      # the same with docker
docker ps --format '{{.Names}}' --filter label=workbench.node       # only compute and GPU nodes
docker exec workbench-slurmctld-1 sinfo -N -l                       # Slurm's view of the nodes
```

![Terminal: docker ps lists workbench-cn001-1 through workbench-gpu002-1, then a command run in cn001 and one as alice in gpu001](img/term-find-and-shell.png)

*Real output from the `min` profile: the six node containers, a command run inside `cn001`, and the simulated user `alice` on `gpu001` with its eight fake GPU device files.*

## Look at one node

```bash
./scripts/benchctl describe node gpu001          # add -o json for scripts
```

It prints, like `kubectl describe`: the container (state, image, memory limit, priority class), Slurm's view (state,
partitions, CPUs, memory, reason when drained), the jobs running on it, the GPUs (how many, which are busy and which
idle, any XID error), the BMC (power, CPU temperature, fans) and how to `ssh` to it. A source that does not answer
becomes one `Note:` line (for example `container not found: is the stack up?`), never an error. The web page shows the
same text in the **Info** card of a node's panel (click the node in the list), without the Docker and Slurm lines (the page has no Docker access by
design).

## Open a shell

```bash
./scripts/benchctl exec slurmctld              # the controller
./scripts/benchctl exec cn001                  # a compute node
./scripts/benchctl exec gpu001                 # a GPU node (fake GPU device files /dev/workbench-gpu0..7)
./scripts/benchctl exec cn001 -u alice         # the same node as a simulated user
./scripts/benchctl exec cn001 -- hostname      # one command, no shell
# the long form, same result: docker exec -it workbench-cn001-1 bash
```

The shell starts as root *inside the container*; that is not root on your host. Simulated users (`alice`, `bob`, from
`sim.yaml`) exist with the same UID on the controller and every node. Exit with `exit` or Ctrl-D.

The simulated users' login shell is `bash`, so a terminal session (a client's terminal, `ssh -p 2222 alice@127.0.0.1`, or
`docker exec -u alice -it ... bash`) has Tab completion for commands, paths and Slurm tools (`squeue`, `sbatch`, `scontrol`...),
the up arrow and Ctrl-R for history, and a `user@node:dir$` prompt. Commands a client runs without a terminal are unaffected.

Useful things to run inside:

```bash
scontrol show node $(hostname)     # this node as Slurm sees it
sbatch --wrap "sleep 60"           # from the controller, as a user: docker exec -u alice ...
ls /dev/workbench-gpu*                # the fake GPUs (GPU nodes only)
```

## Limits to know

- **Nodes can freeze or stop under memory pressure.** The governor pauses (L3) then stops (L4) low-priority containers,
  compute nodes among the first (class P4). A shell that hangs may be a paused container: check
  `./scripts/benchctl status`. Un-pausing happens by itself after 60 s calm, and a stopped node comes back with the
  governor's `start` command or `benchctl up`.
- Nodes are small: each is capped at 60 MB and runs `slurmd` and `munge`, not a full set of system services.
- Scaling down (`benchctl scale 2`) removes nodes, and a shell open on one ends.
- Nodes run `sshd`, reachable only through the login node (`ssh cn001` there, ADR 0014). There is no real NFS or Lustre; storage is faked with metrics only.
- Do not add `--privileged`, extra capabilities or the Docker socket to a container; `benchctl doctor` fails the stack if
  you do, and the governor's watchdog flags it.

## The fidelity VM

One real Ubuntu VM (libvirt/KVM, 2 vCPU, hard cap 2 GB), for what containers cannot prove: real kernel counters, systemd,
`sshd`, real `node_exporter`. It is on demand and is class P6, the first thing the governor saves under pressure.
It needs `vagrant` with the `vagrant-libvirt` plugin.

```bash
cd fidelity-vm
./bin/up.sh                        # memory pre-check, then vagrant up (retries); prints the VM's IP and key path
vagrant ssh fidelity               # a shell, from inside fidelity-vm/
ssh -i .vagrant/machines/fidelity/libvirt/private_key vagrant@<IP>      # plain ssh with the printed IP
./bin/down.sh                      # destroy it
```

## Teleport: `tsh` to the login node and the nodes

The optional **Teleport** part (auth and proxy in one container, about 256 MB limit, [ADR 0020](../adr/0020-teleport-agentless-openssh.md))
lets a client reach the login node and every compute and GPU node through Teleport. Nodes keep their own `sshd` and trust
the Teleport CA (Teleport's agentless OpenSSH mode); no Teleport process runs on a node. It is off by default.

### Steps

**1. Turn it on** (pick one):

```bash
./scripts/benchctl start --access teleport        # from a terminal (adds Teleport to what runs; leaves the page up)
./scripts/benchctl up --with teleport             # the same on a running cluster, without the page
```

or in the page: **☰ Menu → ▦ Cluster architecture → Access → Teleport → Apply** (press twice to confirm), see [the web page](05-web-page.md#teleport-from-the-page). Then check it: the `teleport` card in the entity list says `answering`.

**2. Reach it with an identity file.** Nothing to set up: one file per user in `sim.yaml`, valid for `teleport.cert_ttl` (12 h by default; `benchctl up` writes fresh ones).

```bash
ID=compose/generated/secrets/teleport/identity/alice
tsh --proxy=127.0.0.1:3080 --insecure -i $ID ls                          # the login node and every node
tsh --proxy=127.0.0.1:3080 --insecure -i $ID ssh alice@cn001 hostname    # a command on a node
```

`--insecure` is needed because the proxy's certificate is self-signed. Each `ssh` shows up in the page as a dot from the clients to the Teleport box.

**3. Or use `tsh login` and the browser** (a password and a one-time code, because Teleport 18 cannot turn the second factor off):

```bash
./scripts/benchctl add invite alice     # prints a one-time link
```

Open the link in a browser (accept the certificate warning), choose a password and scan the QR code with an authenticator app (or keep the secret it shows for `oathtool`). Then:

```bash
tsh login --proxy=127.0.0.1:3080 --insecure --user=alice
tsh ls
```

The web UI is at `https://127.0.0.1:3080` (same login). The page lists the address and the command in **Connections** (the Teleport row), and shows Teleport's traffic in the Infrastructure architecture.

![The Infrastructure architecture with Teleport: a dot travels from the clients to the Teleport box for each SSH connection](img/web-infrastructure.png)

**Good to know.** A Teleport container created before the page showed Teleport has no diagnostics port yet, so the card says `not answering` and no dots appear; run `./scripts/benchctl up --with teleport` once to recreate it. Your `tsh` must not be a newer major version than the server (`teleport.version` in `sim.yaml`, default 18.x); use a separate
`TELEPORT_HOME` if you keep other Teleport profiles. Not simulated: the Teleport agent's extras (dynamic labels, session
recording that survives a dropped connection, session sharing), Kubernetes, web and database access.
