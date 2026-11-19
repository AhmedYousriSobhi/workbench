# Tutorials

Each tutorial gets you one result in a few minutes: what you need, numbered steps, what you should see, how to undo it.

Not sure where to start? Do **1. Your first cluster**, then pick the goal you have.

Commands run from the repository root. `./scripts/benchctl` needs no virtualenv activation (see [install](../manual/02-install-and-start.md)).

## Find your goal

**For system administrators and AI infrastructure engineers:** the cluster is a place to practise operating and debugging a GPU Slurm and Kubernetes platform, with real Slurm and real Kubernetes schedulers, without touching production.

### Get started

| I want to... | Tutorial | Time |
|---|---|---|
| start a cluster, look at it, stop it | [1. Your first cluster](01-first-cluster.md) | 10 min |
| choose how many nodes and which parts, and see the memory cost first | [2. Size your cluster](02-size-your-cluster.md) | 5 min |
| open a shell on a node and run a job | [4. A shell and a job](04-shell-and-job.md) | 5 min |
| keep my machine safe while it runs | [10. Keep your machine safe](10-keep-your-machine-safe.md) | 5 min |

### Operate and practise (drills)

| I want to practise... | Tutorial | Time |
|---|---|---|
| a node failing, and Slurm noticing and healing | [3. Break a node](03-break-a-node.md) | 5 min |
| rebooting and power-cycling nodes | [14. Reboot and power-cycle nodes](14-reboot-and-power.md) | 10 min |
| a GPU incident, from alert to ticket | [15. Practise a GPU incident](15-gpu-incident.md) | 15 min |
| writing a node health check | [16. Write a health check](16-write-a-health-check.md) | 10 min |
| QOS limits and preemption | [17. QOS and preemption](17-qos-and-preemption.md) | 10 min |
| a slow or lossy network | [18. Network trouble](18-network-trouble.md) | 5 min |
| restarting or upgrading Slurm under running jobs | [19. Restart and upgrade Slurm](19-restart-and-upgrade-slurm.md) | 15 min |
| a full disk, a quota, a stale mount | [21. Storage trouble](21-storage-trouble.md) | 10 min |
| a failed switch | [22. A switch fails](22-switch-failure.md) | 10 min |
| alerts, Prometheus and Grafana | [23. Alerts and dashboards](23-alerts-and-dashboards.md) | 10 min |

### Kubernetes and access

| I want to... | Tutorial | Time |
|---|---|---|
| use `kubectl` and failing pods | [8. Use Kubernetes](08-use-kubernetes.md) | 10 min |
| meet GPU scheduling problems: fragmentation, preemption, MIG, queues | [20. GPU scheduling on Kubernetes](20-gpu-scheduling-on-kubernetes.md) | 15 min |
| use Teleport (`tsh`, `tsh kube`) | [9. Use Teleport](09-use-teleport.md) | 10 min |

### Test a monitoring client

| I want to... | Tutorial | Time |
|---|---|---|
| point my client at the cluster | [5. Connect your client](05-connect-your-client.md) | 10 min |
| test how it handles SSH trouble | [6. Test SSH handling](06-test-ssh-handling.md) | 10 min |
| test tickets and Jira failures | [7. Test tickets](07-test-tickets.md) | 10 min |

### Configuration, reports and automation

| I want to... | Tutorial | Time |
|---|---|---|
| find out what is wrong, and report it | [11. Fix a problem or report one](11-fix-or-report.md) | 5 min |
| replay a production cluster's configuration | [12. Compare a production config](12-compare-production-config.md) | 15 min |
| drive it from a script or an AI agent | [13. Drive it from a script or an agent](13-script-or-agent.md) | 10 min |

## Which part does what

| You want to test | Start with | Add |
|---|---|---|
| node, GPU and job monitoring | `benchctl start` | tutorials 3, 15, 23 |
| SSH and jump hosts | `benchctl up --profile min` | tutorial 6 |
| tickets | `benchctl start --with jira` | tutorial 7 |
| Kubernetes | `benchctl up --with k8s` | tutorials 8, 20 |
| Teleport | `benchctl up --with teleport --with k8s` | tutorial 9 |

More depth: the [manual](../manual/README.md) explains every part, the [guides](../guides/getting-started.md) cover one topic each, and [which command do I use?](../manual/11-which-command.md) lists commands by goal.
