# 2. Install and start

## Prerequisites

- Ubuntu with cgroup v2, a kernel that exposes memory PSI (`/proc/pressure/memory`), 16 GB RAM, free disk for images.
- Docker (daemon and compose plugin) that **your user** can run without `sudo`.
- Python 3.11 or newer (Ubuntu's `python3`; there is no `python`).
- Optional: KVM and libvirt for the fidelity VM; `k3d` and `kubectl` once Kubernetes lands.

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
bigger profile. The login link signs you in once; keep it private, it only works on this machine.

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
.venv/bin/python -m webui                     # prints the login link
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
