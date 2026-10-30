<p align="center"><img src="docs/assets/logo.svg" alt="workbench logo: servers linked in a W on a bench" width="110"></p>

# workbench

**A pretend HPC cluster for a 16 GB laptop, with a bouncer that keeps it from eating all your RAM.**

Real clusters cost a lot and break at 3 a.m. This one costs about 1 GB, breaks when you ask, and fakes everything an
HPC monitoring app talks to: Slurm, GPUs, Grafana, Jira, BMCs. Built to test a desktop monitoring client, handy for
anyone who wants a cluster to poke without a data centre.

## 1. Wake it up (2 minutes, no sudo)

```bash
./scripts/benchctl start          # checks your machine, builds, starts, prints a link
```

The first run builds images, so go and get a coffee. Open the link it prints. You are looking at six nodes, four fake
services and one very watchful memory governor.

## 2. Poke it

```bash
./scripts/benchctl get nodes                     # who is alive
./scripts/benchctl describe node gpu001          # everything about one node
./scripts/benchctl exec gpu001 -u alice          # a shell on it, tab completion included
./scripts/benchctl scenario run node-down        # now break something, politely
```

In the web page press **Fault actions** on a node and watch it turn grey. **Clear faults** makes it all better.

## 3. Build your own cluster

Want 2 CPU nodes, 4 GPU nodes and Jira? Ask what it costs first, then build only if it fits:

```bash
./scripts/benchctl plan --cpu-nodes 2 --gpu-nodes 4 --with jira
./scripts/benchctl up   --cpu-nodes 2 --gpu-nodes 4 --with jira
```

## 4. Point your app at it

```bash
./scripts/benchctl export profile      # writes a file your monitoring client can import
./scripts/benchctl ssh-key install     # puts the login key where the client looks
```

Then follow [client setup](docs/guides/client-setup-by-component.md), one component at a time.

## 5. Put it back

```bash
./scripts/benchctl stop                # everything you started, and nothing else
```

## When it sulks

| Symptom | Try |
|---|---|
| Start refuses with `REFUSED` | Your RAM is busy (browsers, IDE). Close a few, run it again. The governor is protecting you |
| Not sure your machine qualifies | `./scripts/check-machine.sh` |
| Something odd | `./scripts/benchctl diag` writes one masked file to attach to a bug report |
| Forgot a command | `./scripts/benchctl --help`, or press Tab (`./scripts/benchctl completion install` once): it completes verbs, nodes, profiles and more |

Every command is explained, with its limits, in [the verbs guide](docs/guides/benchctl-verbs.md).

## How it works, in four lines

Most of what an HPC app talks to is an API, not a real machine, so workbench fakes the APIs (one process plays many BMCs
and GPU exporters) and runs Slurm in small containers. A **governor** sits outside everything and pauses, then stops,
low-priority containers before memory runs out. Every fault is a named scenario. Details: [architecture](docs/architecture/README.md).

## Read more

| I want | Go to |
|---|---|
| A 10 minute walk-through | [Getting started](docs/guides/getting-started.md) |
| Everything, in order | [User manual](docs/manual/README.md) |
| What works today | [Progress](docs/PROGRESS.md) |
| The rules it must obey | [SPEC.md](SPEC.md) |
| To contribute | [CLAUDE.md](CLAUDE.md): work by issue and branch, PR into `dev` |

Dual-licensed under [Apache-2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT). Slurm is built locally from its pinned upstream
source (ADR 0002) and is not redistributed.
