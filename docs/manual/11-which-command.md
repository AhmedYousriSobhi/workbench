# 11. Which command do I use?

workbench has a launcher, a CLI, a scenario engine, a governor and a few test scripts. This page says which one to reach
for, and why. Every command runs from the repository root.

## The short answer

| I want to... | Run | Why this one |
|---|---|---|
| start everything, including the web page | `./scripts/benchctl start` | checks the machine, starts the memory governor, the cluster and the web page |
| start only the cluster (for example to test SSH) | `./scripts/benchctl up --profile min` | the cluster alone; no web page, no governor |
| see what is running | `./scripts/benchctl get nodes` and `./scripts/benchctl status` | the nodes and their state; the governor level |
| open the web page | `./scripts/benchctl open` | prints the login link |
| stop everything | `./scripts/benchctl stop` | stops the web page, the cluster and the governor it started |
| stop only the cluster | `./scripts/benchctl down` | touches only the `hpcsim` project |
| see everything about one node | `./scripts/benchctl describe node gpu001` | container, Slurm state, jobs, which GPUs are busy, BMC readings ([chapter 4](04-nodes-and-shells.md)) |
| open a shell on a node | `./scripts/benchctl exec cn001` (or `ssh` through the login node) | the login node is the way in, like a real cluster; the exact `ssh` line is in [chapter 4](04-nodes-and-shells.md) |
| break something on purpose | `./scripts/benchctl scenario run NAME` | plays a scripted fault ([chapter 6](06-scenarios.md)) |
| see what a scenario would do first | `./scripts/benchctl scenario run NAME --dry-run` | prints the steps, touches nothing |
| list the scenarios | `./scripts/benchctl scenario list` | name, steps, description |
| click a fault on one node | the web page, Cluster tab | the same steps without the terminal ([chapter 5](05-web-page.md)) |
| see why a start was refused | `./scripts/benchctl status` and `./scripts/benchctl preflight --profile min` | the governor level and whether the profile fits |
| check my machine is ready | `./scripts/benchctl check` | OS, RAM, disk, Docker, ports |
| give a client that only reads `~/.ssh` the login key | `./scripts/benchctl ssh-key install` (and `remove` after) | copies the key to `~/.ssh/workbench_ed25519`, mode 600 |
| import the cluster into the client in one step | `./scripts/benchctl export profile` | writes a profile file; only the key and token are set by hand ([guide](../guides/export-profile.md)) |
| report a problem | `./scripts/benchctl diag` | one masked tarball to attach |

## `start` or `up`?

- `benchctl start` is the **one-command launcher**. Use it when you want a working setup and the web page. Its default
  profile is `min`.
- `benchctl up` is the **cluster alone**: one specific job. `start` calls it as its last step. Its default profile is
  `std`, so pass `--profile min` on a small machine. Related verbs: `down`, `scale`, `scenario`, `status`, `diag`.

Without `benchctl start` the governor is not started, so nothing protects the host's memory: on a 16 GB machine prefer
`start`. (The old names `./scripts/opti` and `simctl` still work as aliases of these.)

## Which profile?

| Profile | Use it for |
|---|---|
| `min` | everyday testing of a client: 4 compute nodes, the mocks, the login node (planned budget 1 GB) |
| `std` | `min` plus accounting and load, 16 compute nodes (planned budget 6 GB) |
| `full` | `std` with 32 compute nodes (planned budget 8.5 GB), only on a machine with room |

`./scripts/benchctl preflight --profile std` tells you whether it fits before you start.

## Scenarios, faults and the web page

- A **scenario** is a file in `scenarios/` with timed steps. `scenario run` plays it from the terminal and can
  drive Docker (Slurm drains, SSH faults).
- The **web page** plays the same scenarios, but has no Docker access, so it **skips** Slurm and SSH steps. To
  play those, use the terminal.
- A **fault action** on a node card (power off, fan failure, XID) is one step applied right now.
- To test a client's SSH handling, follow [SSH connection testing](../guides/ssh-connection-testing.md).

## Checking your own changes

| I changed... | Run |
|---|---|
| any code or docs, before a commit | `./scripts/validate.sh` |
| something that touches a live container or the governor, before asking for a merge | `./scripts/verify.sh` (add `--full` for the whole stack lifecycle, or `--only KEYWORD`) |
| the governor's cgroup or systemd code | `sudo ./scripts/real-test-root.sh` |

`verify.sh --full` starts and stops the stack, so run it when nothing else is using the stack.

## When something refuses to start

| You see | Meaning | Do |
|---|---|---|
| `up` says REFUSED | the governor is at level L2 or higher, or memory is low | close browsers, check `./scripts/benchctl status`, retry |
| port 8080 already in use | another program holds the Jira mock's port | stop it, or see [troubleshooting](09-troubleshooting.md) |
| `fault` or `scenario run` says "No such container" | the stack is not running | `./scripts/benchctl up --profile min` |

More symptoms and fixes: [troubleshooting](09-troubleshooting.md). Every flag and file: [reference](10-reference.md).
