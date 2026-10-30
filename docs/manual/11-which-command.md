# 11. Which command do I use?

opti-sim has a launcher, a CLI, a scenario engine, a governor and a few test scripts. This page says which one to reach
for, and why. Every command runs from the repository root.

## The short answer

| I want to... | Run | Why this one |
|---|---|---|
| start everything, including the web page | `./scripts/opti start` | checks the machine, starts the memory governor, the cluster and the web page |
| start only the cluster (for example to test SSH) | `./scripts/simctl up --profile min` | the cluster alone; no web page, no governor |
| see what is running | `./scripts/opti status` | governor, cluster and web page in one view |
| open the web page | `./scripts/opti open` | prints the login link |
| stop everything | `./scripts/opti stop` | stops the web page, the cluster and the governor it started |
| stop only the cluster | `./scripts/simctl down` | touches only the `hpcsim` project |
| open a shell on a node | `docker exec -it hpcsim-cn001-1 bash`, or `ssh` through the login node | the login node is the way in, like a real cluster; the exact `ssh` line is in [chapter 4](04-nodes-and-shells.md) |
| break something on purpose | `./scripts/simctl scenario run NAME` | plays a scripted fault ([chapter 6](06-scenarios.md)) |
| see what a scenario would do first | `./scripts/simctl scenario run NAME --dry-run` | prints the steps, touches nothing |
| list the scenarios | `./scripts/simctl scenario list` | name, steps, description |
| click a fault on one node | the web page, Cluster tab | the same steps without the terminal ([chapter 5](05-web-page.md)) |
| see why a start was refused | `./scripts/simctl status` and `./scripts/simctl preflight --profile min` | the governor level and whether the profile fits |
| check my machine is ready | `./scripts/simctl check` | OS, RAM, disk, Docker, ports |
| give a client that only reads `~/.ssh` the login key | `./scripts/simctl ssh-key install` (and `remove` after) | copies the key to `~/.ssh/opti-sim_ed25519`, mode 600 |
| report a problem | `./scripts/simctl diag` | one masked tarball to attach |

## `opti` or `simctl`?

- `opti` is the **one-command launcher**. Use it when you want a working setup and the web page. Its default profile
  is `min`.
- `simctl` is the **CLI underneath**. `opti start` calls `simctl up` as its last step. Use `simctl` for one specific
  job: `up`, `down`, `scale`, `scenario`, `status`, `diag`. Its `up` default profile is `std`, so pass
  `--profile min` on a small machine.

If you only need the cluster, `simctl up --profile min` is enough. Without `opti start` the governor is not started, so
nothing protects the host's memory: on a 16 GB machine prefer `opti start`.

## Which profile?

| Profile | Use it for |
|---|---|
| `min` | everyday testing of a client: 4 compute nodes, the mocks, the login node (planned budget 1 GB) |
| `std` | `min` plus accounting and load, 16 compute nodes (planned budget 6 GB) |
| `full` | `std` with 32 compute nodes (planned budget 8.5 GB), only on a machine with room |

`./scripts/simctl preflight --profile std` tells you whether it fits before you start.

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
| `up` says REFUSED | the governor is at level L2 or higher, or memory is low | close browsers, check `./scripts/simctl status`, retry |
| port 8080 already in use | another program holds the Jira mock's port | stop it, or see [troubleshooting](09-troubleshooting.md) |
| `fault` or `scenario run` says "No such container" | the stack is not running | `./scripts/simctl up --profile min` |

More symptoms and fixes: [troubleshooting](09-troubleshooting.md). Every flag and file: [reference](10-reference.md).
