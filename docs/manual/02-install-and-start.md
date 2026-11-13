<p align="center"><img src="../assets/art/02-install-and-start-hero.svg" alt="Four steps in a row, each with a tick: check the machine, tools, governor, cluster" width="720"></p>

# 2. Install and start

**In 30 seconds:** one command checks your machine, installs the tools, starts the memory governor and the cluster, and prints a link to the web page. It needs no `sudo`.

## Do it

```bash
./scripts/benchctl start
```

Open the link it prints. The sections below list the prerequisites and the same steps one at a time.

## Prerequisites

- Ubuntu with cgroup v2, a kernel that exposes memory PSI (`/proc/pressure/memory`), 16 GB RAM, free disk for images.
- Docker (daemon and compose plugin) that **your user** can run without `sudo`.
- Python 3.11 or newer (Ubuntu's `python3`; there is no `python`).
- Optional: KVM and libvirt for the fidelity VM. Kubernetes needs no tool on the host: `kubectl` runs from the official image.

Check all of this before building anything:

```bash
./scripts/check-machine.sh      # needs only system python3; ends READY or NOT READY with a fix line per problem
```

## The one-command way

```bash
./scripts/benchctl start            # machine check, tools, governor, cluster (min), web page; prints the login link
./scripts/benchctl status           # governor, cluster and web page at a glance
./scripts/benchctl open             # print the login link again (opens a browser if there is a display)
./scripts/benchctl stop             # stop the web page, the cluster and your own governor
```

The first `start` builds images (a few minutes) and creates `.venv`. Use `./scripts/benchctl start --profile std` for a
bigger profile. The login link signs you in once and expires after a minute (the token is never in the URL, so it stays out of browser history); `benchctl open` prints a new one. Without a link, open `/login` and paste the token from `compose/generated/secrets/ui_token`.

`benchctl start` reads two environment variables: `BENCHCTL_UI_PORT` (web page port, default 8088) and `BENCHCTL_GOVERNOR`
(`auto|user|system|off`, default `auto`: the system service if it runs, otherwise a governor started as you).
`off` disables memory protection; do not use it on a workstation you care about.

## The step-by-step way

Same result, one visible step at a time:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'   # once; venv is per shell
./scripts/benchctl doctor                       # host prerequisites; fix any FAIL first
./scripts/benchctl preflight --profile min      # would this profile fit in free memory right now?
./scripts/benchctl up --profile min             # governor admission, ordered start (about a minute)
.venv/bin/python -m webui                     # then ./scripts/benchctl open for the login link
```

Without activating the venv, prefix commands with `./scripts/benchctl` instead of `python -m benchctl.cli`.

## Stopping and cleaning up

```bash
./scripts/benchctl stop             # everything `benchctl start` began
./scripts/benchctl down           # only the cluster; removes the workbench compose project and nothing else
```

Both touch only the `workbench` project, never your other containers.

## Optional: the root governor service

```bash
sudo ./scripts/install-governor.sh              # starts at boot, adds cgroup throttle and reclaim
sudo ./scripts/install-governor.sh --uninstall
```

You can skip it. See [the governor](07-governor.md) for what it adds.

## What you will see

`start` prints four numbered steps (`1/4 checking the machine` to `4/4 starting the cluster`), then the login link and how to stop everything. `./scripts/benchctl status` then shows the governor, the cluster and the web page at a glance.

## Under the hood

`benchctl start` is a launcher over the same commands you can run by hand. In order:

1. **Check the machine** with `scripts/check-machine.sh` (stops with the FAIL lines).
2. **Tools:** creates `.venv` once and installs the project into it.
3. **Governor:** uses the system service if it runs, otherwise starts one as you (`BENCHCTL_GOVERNOR`).
4. **Cluster:** `benchctl up`, which asks the governor to admit the memory first (GV-9), builds only the images whose inputs changed, then starts the services in stages (observability, Slurm core, Slurm services, mocks, optional parts, BMC addresses, nodes, login node) and waits until each answers.

Then it starts the web page in the background and prints a one-time login link.

> [!HOOD]
> Image builds are skipped when a hash of the build context and its base image matches the stamp from the last build, and a stage counts as started only when its services answer, not when the container is merely running (issue 449).

Source: `scripts/benchctl-launch`, `benchctl/stack.py`, `benchctl/ready.py`, ADR 0010, SPEC GV-9.

## Tips and gotchas

> [!TIP]
> Lost the link? `./scripts/benchctl open` prints a new one; a link signs you in once and expires after a minute.

> [!WARNING]
> `BENCHCTL_GOVERNOR=off` turns memory protection off. Do not use it on a workstation you care about.

## If it goes wrong

| You see | Why | Do this |
|---|---|---|
| `NOT READY` from the machine check | a prerequisite is missing | follow the fix line printed for each problem |
| `REFUSED` from `up` | free memory is too low for the profile | close a few programs, run it again |
| "the web page did not start" | the port is busy, often an older page started by hand | stop that page, then run `start` again; `BENCHCTL_UI_PORT` picks another port |
| anything else | | `./scripts/benchctl diag` writes one masked file to attach to a bug report |

## Learn more

[Using the cluster](03-using-the-cluster.md) · [The governor](07-governor.md) · [Troubleshooting](09-troubleshooting.md) · [Getting started](../guides/getting-started.md)
