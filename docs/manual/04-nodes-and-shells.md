# 4. Nodes and shells

**You reach a simulated node by SSH through the login node**, or open a shell with `docker exec`. Every node runs a small
`sshd` (key login only, no root, no port published on your machine); the login node on `127.0.0.1:2222` is the way in
([ADR 0014](../adr/0014-node-sshd-and-ssh-fault-scenarios.md)). The other machine with a real `sshd` is the optional
[fidelity VM](#the-fidelity-vm).

## SSH to a node

```bash
KEY=compose/generated/secrets/ssh/id_ed25519
ssh -i $KEY -p 2222 alice@127.0.0.1 ssh cn001 hostname     # a hop from the login node: prints cn001
ssh -i $KEY -p 2222 alice@127.0.0.1 srun -N1 -w cn001 hostname
ssh -tt -i $KEY -p 2222 alice@127.0.0.1 srun --pty -N1 -w gpu001 bash    # an interactive shell on a GPU node
```

From your own machine, a client reaches a node through the login node (ProxyJump or a forwarded channel to `cn001:22`),
using the same key. The login node already trusts every node's host key, so a first hop does not prompt.

## Find the nodes

Node names come from [sim.yaml](../../sim.yaml): compute nodes `cn001`, `cn002`, ... and GPU nodes `gpu001`, `gpu002`, ...
Each container is named `hpcsim-<name>-1`.

```bash
docker ps --format '{{.Names}}' --filter label=hpcsim.class      # every opti-sim container
docker ps --format '{{.Names}}' --filter label=hpcsim.node       # only compute and GPU nodes
docker exec hpcsim-slurmctld-1 sinfo -N -l                       # Slurm's view of the nodes
```

![Terminal: docker ps lists hpcsim-cn001-1 through hpcsim-gpu002-1, then a command run in cn001 and one as alice in gpu001](img/term-find-and-shell.png)

*Real output from the `min` profile: the six node containers, a command run inside `cn001`, and the simulated user `alice` on `gpu001` with its eight fake GPU device files.*

## Open a shell

```bash
docker exec -it hpcsim-slurmctld-1 bash        # the controller
docker exec -it hpcsim-cn001-1 bash            # a compute node
docker exec -it hpcsim-gpu001-1 bash           # a GPU node (fake GPU device files /dev/hpcsim-gpu0..7)
docker exec -u alice -it hpcsim-cn001-1 bash   # the same node as a simulated user
docker exec hpcsim-cn001-1 hostname            # one command, no shell
```

The shell starts as root *inside the container*; that is not root on your host. Simulated users (`alice`, `bob`, from
`sim.yaml`) exist with the same UID on the controller and every node. Exit with `exit` or Ctrl-D.

Useful things to run inside:

```bash
scontrol show node $(hostname)     # this node as Slurm sees it
sbatch --wrap "sleep 60"           # from the controller, as a user: docker exec -u alice ...
ls /dev/hpcsim-gpu*                # the fake GPUs (GPU nodes only)
```

## Limits to know

- **Nodes can freeze or stop under memory pressure.** The governor pauses (L3) then stops (L4) low-priority containers,
  compute nodes among the first (class P4). A shell that hangs may be a paused container: check
  `./scripts/simctl status`. Un-pausing happens by itself after 60 s calm, and a stopped node comes back with the
  governor's `start` command or `simctl up`.
- Nodes are small: each is capped at 60 MB and runs `slurmd` and `munge`, not a full set of system services.
- Scaling down (`simctl scale 2`) removes nodes, and a shell open on one ends.
- Nodes run `sshd` only when the profile has a login node, and only the login node can reach it. There is no real NFS or Lustre; storage is faked with metrics only.
- Do not add `--privileged`, extra capabilities or the Docker socket to a container; `simctl doctor` fails the stack if
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

## Not built: Teleport to the simulated nodes

The application under test is meant to reach nodes through Teleport (issue #77). That needs decisions that are still
open (which access types, real Teleport or a mock). Plain SSH through the login node works (above).
