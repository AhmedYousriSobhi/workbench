# Getting started

How to run workbench on your workstation and see it working. Keep this page in step with the commands:
`tests/test_docs.py` fails if a `benchctl` command named here no longer exists. For everything about the tool, read the
[user manual](../manual/README.md).

## Quickest way

```bash
./scripts/benchctl start      # machine check, tools, cluster, web page; prints the login link
./scripts/benchctl get nodes  # every node and its state
./scripts/benchctl stop       # stop the web page and the cluster
```

### What `start` builds

| You type | You get |
|---|---|
| `start` | the workbench you built last, rebuilt as it was (the first line names the counts and parts it keeps); the small `min` preset if you built nothing before |
| `start --profile min` | back to the small default (naming a size or parts replaces what you built, and the first line says so) |
| `start --with jira` | the default parts plus Jira |
| `start --ticketing jira --hardware bmc,bcm --access teleport` | optional parts by category (Tab completes the flags) |
| `start --addons` | every optional part |

To pick your own mix (for example 2 CPU and 4 GPU nodes with Jira) and see what it costs first, use `benchctl plan` and
`benchctl up --cpu-nodes ...` ([choose the workbench](choose-workbench.md); what each part uses and what to have free:
[memory](memory.md)).

### How commands look

Every command has the same shape, like `kubectl`: `benchctl VERB [RESOURCE] [NAME]`.

```bash
benchctl get nodes
benchctl exec gpu001 -- nvidia-smi
benchctl ssh alice@cn001
benchctl scenario run node-down
```

`benchctl --help` lists the verbs, and the limits of every verb are in [the verbs guide](benchctl-verbs.md). A mistyped
verb gets a `Did you mean` hint and is never run. Tab completion: `./scripts/benchctl completion install`, then open a
new terminal (`completion remove` undoes it).

### The first start is slow

- The first start builds the images: a few minutes, because Slurm compiles on every core. A compiler cache in Docker's
  build cache makes the next compile of a changed or bumped Slurm much shorter.
- After an update that touches an image, `start` rebuilds only what changed: about 20 s for the shared base, under a second later.
- On a busy machine, limit the compile:

```bash
docker compose -f compose/generated/docker-compose.yml build --build-arg BUILD_JOBS=4 cn001
```

The numbered sections below are the same steps one at a time, for when you want to see or change each.

## 0. Check the machine (before building anything)

```bash
./scripts/check-machine.sh
```

It needs only the system `python3`. It checks:
- the OS, CPU cores, RAM and free disk;
- cgroup v2;
- Docker (daemon and compose);
- free ports;
- optional tools (KVM, k3d, kubectl).

It also says which profiles fit in the memory available right now.

It ends with `READY` or `NOT READY`, and a fix line for each problem. Do not build until it says `READY`.

The same check runs as `./scripts/benchctl check` once the venv exists.

## 0b. Install the tools (once)

Ubuntu has `python3`, not `python`, and `benchctl` needs a few packages. Use a virtualenv in the repo:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
```

The venv is per shell. You have two choices:
- Activate it again in every new terminal: `. .venv/bin/activate`. Then type `benchctl ...`.
- Skip activation: `./scripts/benchctl <command>` uses `.venv` directly, for example `./scripts/benchctl status`.

Every command below works both ways. The governor service needs no packages. It runs on the system `python3`.

## 1. Check the setup (doctor)

```bash
./scripts/benchctl doctor        # cgroup v2, PSI, Docker, swap, memlock, containers stay unprivileged, and a tsh that is too new for Teleport
./scripts/benchctl preflight --profile min    # would this profile fit in free memory right now?
```

Doctor then prints one block per host setting that is not as recommended (zram swap, `vm.swappiness`, THP `madvise`). Each block has:

- the temporary command (lost at reboot), what it changes and the undo;
- a `persist:` form that survives reboot, with its undo (a sysctl.d drop-in, a zram-generator config, the
  `transparent_hugepage=madvise` kernel option).

Doctor only prints. It never runs `sudo` or changes the host, so copy a block into your own shell if you want it.

Scripts read the same data from `doctor --json` (`tuning`).

- A `WARN` for `virsh`, `vagrant` or `tc` is fine. Only optional parts need them (Kubernetes needs no host tool).
- Fix any `FAIL` first.

If `tsh` is installed, doctor also says whether it is newer than the Teleport server (`teleport.version` in `sim.yaml`).
- Teleport supports a `tsh` of the same or one older major version.
- A newer one is refused with an unclear error.
- `benchctl up` prints the same one-line warning when Teleport is on.

## 2. Start the governor (outside monitoring and control)

The governor watches memory and security and may stop low-priority containers under pressure. `./scripts/benchctl start`
starts it for you, **as your own user, no sudo**: it pauses and stops containers through Docker. (Rootless mode cannot
throttle with `memory.high`, reclaim, or guarantee its own memory is never swapped; ADR 0010.) Look at it with:

```bash
./scripts/benchctl status               # level, mode (user or root), memory, groups, security findings
```

For full protection, an optional root service that starts at boot and uses cgroup files:

```bash
sudo ./scripts/install-governor.sh        # optional; benchctl start then uses it instead of its own
sudo ./scripts/install-governor.sh --uninstall
```

## 3. Start, scale and stop the cluster

```bash
./scripts/benchctl up --profile min     # pre-flight, governor admission, ordered start (~1 min)
./scripts/benchctl scale 2              # compute nodes (slurm.conf regenerated, slurmd started/stopped)
./scripts/benchctl down                 # stops only the workbench project; your other containers are untouched
```

`up` builds every image once, then starts the stack in stages.
- A stage starts only when the services before it answer: the controller to `scontrol ping`, the mocks to `/healthz`. A running container is not enough. A node therefore never starts before the controller is ready.
- If a service does not answer in time, the message names it.
- `up` ends with the total and the seconds per stage (`stack is up in 6 s (observability 0, slurm core 0, ...)`).

Profiles are in `profiles/` (`min`, `std`, `full`).
- `up` refuses a profile that does not fit, and names one that does.
- Components not built yet are skipped with a note.

## 4. What to look at

| What | Where |
|---|---|
| Grafana dashboards and governor annotations | http://127.0.0.1:3000 (user `admin`, password `admin`). For a monitoring client: datasource uid `prometheus`, GPU host label `Hostname`, and a read-only service-account token in `compose/generated/secrets/grafana_viewer_token` |
| Prometheus | http://127.0.0.1:9090 (the `governor` job scrapes the governor's `/metrics`: `workbench_governor_level`, `workbench_host_mem_available_bytes`, ...) |
| Alertmanager | http://127.0.0.1:9093. Six default rules (`observability/rules.yml`) fire on injected faults; every alert opens one ticket in the Jira mock (`curl http://127.0.0.1:8080/sim/tickets`). Try it: `./scripts/benchctl scenario run gpu-xid-storm`, then `./scripts/benchctl get alerts`. Needs the Jira part for the tickets (on by default) |
| Fake BMCs (Redfish) | `curl http://127.0.0.1:8000/cn001/redfish/v1/Systems/1`; standard TLS surface for real clients: `curl -k --resolve bmc-cn001:8443:127.0.0.1 https://bmc-cn001:8443/redfish/v1` (login with the account in `compose/generated/secrets/bmc_credentials`, ADR 0012) |
| Fake BCM (CMDaemon JSON) | `curl -X POST http://127.0.0.1:8081/json -d '{"service": "cmdevice", "call": "getDevices"}'` |
| Fake Jira | `curl -X POST http://127.0.0.1:8080/rest/api/2/issue -d '{"fields": {"project": {"key": "OPS"}, "summary": "hi"}}'`; failures: `curl -X POST http://127.0.0.1:8080/sim/failure -d '{"mode": "429", "count": 3}'` |
| Slurm jobs | `docker exec workbench-slurmctld-1 sinfo` / `squeue` / `sbatch` |
| Slurm REST | token: `docker exec workbench-slurmctld-1 scontrol token username=alice`; see ADR 0003 |
| Teleport (when on) | `https://10.141.255.30:3080`; `tsh --proxy=10.141.255.30:3080 --insecure -i compose/generated/secrets/teleport/identity/alice ls`; steps in [Nodes and shells](../manual/04-nodes-and-shells.md#teleport-tsh-to-the-login-node-and-the-nodes) |
| Real DCGM exporter (when on) | `./scripts/benchctl up --with dcgm`, then `curl http://127.0.0.1:9402/metrics` (details below) |
| Real GPU (when on) | `./scripts/benchctl up --with real-gpu`, then `curl http://127.0.0.1:9403/metrics` (details below) |
| Build progress and diagrams | `docs/progress.html` |

Inject a fault and watch it show up:

```bash
curl -X POST http://127.0.0.1:8000/sim/inject -d '{"node": "gpu001", "temp": 95, "fan_fail": 2}'
curl http://127.0.0.1:8000/gpu001/redfish/v1/Chassis/1/Thermal
curl -X POST http://127.0.0.1:8000/sim/inject -d '{"node": "gpu001", "clear": true}'
```

Or run a named scenario (a timed list of faults) instead of injecting by hand:

```bash
./scripts/benchctl scenario list
./scripts/benchctl scenario run node-down --dry-run   # show the timeline, change nothing
./scripts/benchctl scenario run node-down --speed 10  # play it ten times faster
```

A scenario fails (exit 1) when a step errors or an `expect` line does not hold. Steps the caller cannot run (the web page has no Docker) are reported as skipped.

### Real DCGM exporter (optional)

```bash
./scripts/benchctl up --with dcgm
curl http://127.0.0.1:9402/metrics
```

- The official dcgm-exporter on NVIDIA's mock NVML stands for the first GPU node (about 125 MB, off by default; the build needs network once).
- `--dcgm-nodes 3` gives the first three GPU nodes one each (ports 9402, 9410, 9411; `docker exec workbench-dcgm-exporter-gpu002-1 ...` for the second).
- It follows Slurm jobs and injected faults. A scenario `clear` restarts the exporter (real DCGM keeps the last XID until its host engine restarts).
- A node powered off stops its exporter, so scraping fails as for a dead host. Both need Docker, so the web page's buttons leave the exporter as it is.
- `dcgmi` works inside the container, and on the GPU node it stands for (a node with no real exporter has no host engine to ask):

```bash
docker exec workbench-dcgm-exporter-1 dcgmi discovery -l
docker exec workbench-gpu001-1 dcgmi discovery -l
```

The hand-written exporter on `:9400` stays for every node. See ADR 0025.

### Real GPU (optional)

Needs a machine with an NVIDIA card, its driver and the container toolkit with a CDI spec
(`sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`). Without a card it stops with one message.

```bash
./scripts/benchctl up --with real-gpu
curl http://127.0.0.1:9403/metrics                          # real series from the real card, Hostname="rgpu001"
docker exec workbench-rgpu001-1 dcgmi discovery -l          # on the node, through the exporter's host engine
docker exec workbench-real-gpu-1 nvidia-smi
```

It also adds the Slurm node `rgpu001` on the card, in its own partition:

```bash
docker exec -u alice workbench-login-1 sbatch -p real-gpu --gres=gpu:1 --wrap="nvidia-smi -L"
srun -p real-gpu --gres=gpu:1 nvidia-smi -L        # interactively, on the login node
```

Always name the partition. `srun` defaults to `cpu`, and `--nodelist rgpu001` alone answers "Requested node configuration is not available".

With the BMC farm and BCM mock on (`--with bmc --with bcm`), the node is listed there too:
- a Redfish system whose `Oem.GpuModel` is the card's model;
- a BCM device in category `gpu` with 1 GPU.

It costs about 480 MB and is never in `all`. SPEC C-23, ADR 0037.

## 4a. Get a shell on a node

The compute nodes run `sshd` but publish no port, so the **login node** is the SSH entry point (below); from it `ssh cn001` works.

```bash
ssh alice@login ping -c1 slurmctld
docker ps --format '{{.Names}}' --filter label=workbench.node     # list the compute and GPU nodes
docker exec -it workbench-cn001-1 bash                            # a shell on a compute node (exit to leave)
docker exec -u alice -it workbench-gpu001-1 bash                  # the same, as a simulated user
```

Nodes are named `cn001`, `gpu001`, ... and their containers `workbench-<name>-1`.

- Every node, login node and the controller carry the usual troubleshooting tools (`ping`, `ip`, `ss`, `dig`, `nc`,
  `traceroute`, `tcpdump`, `curl`, `ps`, `lsof`, `strace`, `vi`; no sudo and no root login, by design).
- `tcpdump` is the exception: it needs raw-socket rights that SSH users do not have ("Operation not permitted"), so run it as root through Docker: `docker exec workbench-cn001-1 tcpdump -i any -c 5 -n`.
- Administrative Slurm commands (`scontrol update`) need the controller container: `docker exec workbench-slurmctld-1 scontrol update nodename=cn001 state=resume`.

For tools that connect over SSH (a desktop monitoring client), `benchctl up` starts a `login` container:

- it listens on `127.0.0.1:2222`, key login only, with the Slurm client tools on PATH;
- the key is `compose/generated/secrets/ssh/id_ed25519` (ADR 0011);
- more login nodes, and Teleport-only ones: `benchctl up --login ssh,teleport`, see [Nodes and shells](../manual/04-nodes-and-shells.md#login-nodes-how-many-and-how-each-one-lets-you-in);
- Slurm's munge and JWT keys are made by `benchctl up` in `compose/generated/secrets/slurm` (never in an image; delete the folder and run `up` to rotate them).

```bash
ssh -i compose/generated/secrets/ssh/id_ed25519 -o IdentitiesOnly=yes -p 2222 alice@127.0.0.1 sinfo
ssh -i compose/generated/secrets/ssh/id_ed25519 -o IdentitiesOnly=yes -p 2222 alice@127.0.0.1 ssh cn001 hostname   # a hop to a node
```

A client that only accepts keys under `~/.ssh`:
- `./scripts/benchctl ssh-key install` copies the key to `~/.ssh/workbench_ed25519`.
- `./scripts/benchctl ssh-key remove` removes it afterwards.

Next:

- See what the output looks like in [Nodes and shells](../manual/04-nodes-and-shells.md).
- A shell that hangs may be a node the governor paused under memory pressure (`./scripts/benchctl status`).
- The other machine with a real `sshd` is the optional fidelity VM (`cd fidelity-vm && ./bin/up.sh`, then `vagrant ssh fidelity`).
- Teleport access (`./scripts/benchctl up --with teleport`, then `tsh ... ls`) is in [Nodes and shells](../manual/04-nodes-and-shells.md#teleport-tsh-to-the-login-node-and-the-nodes).

Two optional nodes:
- A **controller node** is where slurmctld runs. Only whitelisted admins get in, and it is the BCM head node.
- A **jump host** is a bare bastion in front of the login nodes.

```bash
./scripts/benchctl up --controller --access jump           # 24 MB each; add --access teleport for Teleport to the controller too
ssh -i compose/generated/secrets/ssh/id_ed25519 -o IdentitiesOnly=yes -p 2221 alice@127.0.0.1 sinfo      # alice is in the admins group; bob is refused
ssh -i compose/generated/secrets/ssh/id_ed25519 -o IdentitiesOnly=yes -o ProxyCommand='ssh -i compose/generated/secrets/ssh/id_ed25519 -o IdentitiesOnly=yes -W %h:%p -p 2220 alice@127.0.0.1' alice@10.141.255.254 hostname   # through the jump host (`-J` alone would not pass the key to the jump leg)
```

Who may use the controller is set in `sim.yaml` (`controller.allow.users`, `controller.allow.groups`); see [Choose your workbench](choose-workbench.md) and ADR 0036.

Kubernetes (a real control plane with KWOK fake nodes, about 0.3 GB) is optional: `./scripts/benchctl up --with k8s`, then `ssh ... alice@127.0.0.1 kubectl get nodes` (see [Nodes and shells](../manual/04-nodes-and-shells.md#kubernetes-optional-part)).

## 4b. The web page

```bash
.venv/bin/python -m webui        # serves 127.0.0.1:8088; get a one-time sign-in link with ./scripts/benchctl open
```

Open that link once. It sets a cookie; the token is kept in `compose/generated/secrets/ui_token`.
`./scripts/benchctl start` keeps the page's log (`compose/generated/ui.log`) free of the token, and
`./scripts/benchctl open` prints the full link on the terminal.

Everything is on one screen:

| Part | What it shows |
|---|---|
| Top strip | governor level, free memory, healthy services and nodes, any running scenario, then **☰ Menu** (**▶ Scenarios**, **▦ Cluster architecture**, **✚ Health checks**, **⇩ Export**), help and a theme switch (auto, light, dark). Press the level or memory chip for the **Governor** panel |
| *Needs attention* | anything that needs you |
| **Infrastructure architecture** | login nodes, the Teleport proxy, the services and every node, one labelled line per way in (*direct SSH*, *Teleport*, *jump host*). A round dot travels from *Clients* to a service for each real request; a diamond travels from *Faults* to the node it hits |
| **Entity list** | one card per node and per service with its status and quick facts; click a node to open its panel in place of the list |

What you can do on it:
- **Diagram:** hover a part for a tip, click it for a pop-up with its address and command. **Hide node list** gives the diagram the whole width.
- **Dots:** *Pause animation* stops them. They stop by themselves at governor L3 or for reduced motion.
- **Teleport:** when on, it is a box in the diagram and a card in the list. Turn it on with `./scripts/benchctl start --access teleport`, or tick it under **Access** in **▦ Cluster architecture**.
- **A node's panel:** **Actions** power it off, overheat it, fail its fans or inject a GPU error (then *Recover*). **Info** shows how to reach it.
- **▶ Scenarios:** pick a speed, press Play, and watch each step turn ok, skipped or failed. Press Stop to abort.
- **▦ Cluster architecture:** change the number of nodes and apply it.
- **Phone:** the panels stack under each other.

The page skips steps that need Docker (`ssh` and `k8s` steps), because it has no Docker access by design. Use `./scripts/benchctl scenario run` for those. The [web page chapter](../manual/05-web-page.md) has the details.

## 5. When something is wrong

```bash
./scripts/benchctl status         # governor level and SECURITY findings
./scripts/benchctl diag           # one masked tarball: attach it to bug reports
```

If memory is tight the governor pauses then stops containers lowest priority first (P6 down to P2); that
is the design, see [the governor](../architecture/05-governor.md). `up` and `scale` are refused from level L2 up.

## 6. Where next

The [user manual](../manual/README.md) has a chapter for each topic: concepts, the web page, scenarios, the governor, configuration, troubleshooting and a command reference.

## 7. For developers

To look at the web page without a screen: `pip install -e '.[browser]'`, `.venv/bin/python -m playwright install chromium`, then `.venv/bin/python scripts/ui_shots.py` writes screenshots to `/tmp/workbench-ui-shots` and reports problems. The page is plain ES modules (`webui/static/js/`, no build step); its logic tests need Node 22 or newer: `node --test "webui/tests/js/*.test.mjs"` (`validate.sh` runs them).

Once per clone, no sudo:

```bash
./scripts/install-hooks.sh
```

The git hooks it installs:

- no commits on `main` or `dev`;
- an issue reference in every commit message;
- a check that your git identity is your own GitHub noreply address (`./scripts/check-identity.sh`; GitHub credits a commit by the ID in that address, so a placeholder would credit a stranger);
- `validate.sh` before each commit;
- a privacy scan (`./scripts/check-privacy.sh`) so no personal path, email or token is published.

The repository is public, and the Claude skills under `.claude/` are symlinks into a home directory, so that folder is gitignored.

Before asking for a merge:

```bash
./scripts/verify.sh --changed            # only the live tests your change can reach
./scripts/verify.sh --changed --explain  # why those
./scripts/verify.sh --full --with-jira   # before a release; or every night: ./scripts/verify.sh --install-nightly (a user timer, no sudo)
./scripts/verify.sh --unit teleport --clean   # one component (list them with --list-units)
```

- Plain `--full` leaves out the real Jira (about 7 minutes) and says so; see CLAUDE.md.
- Every step prints its seconds and a `TIME` line closes the run.
- `validate.sh` is skipped when it already passed for this exact tree (the commit hook ran it); `--revalidate` forces it, `--skip-browser` leaves out the Chromium tests.
- The full run stops your cluster and sets aside your saved selection first, then puts the selection back; run `./scripts/benchctl up` afterwards.
