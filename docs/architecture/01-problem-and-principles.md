# Problem and design principles

[Architecture index](README.md) · [Project overview](../../README.md)

## 1. Problem and diagnosis

| Today | Why it hurts on 16 GB |
|---|---|
| Vagrant VM for compute + Grafana node | Every VM carries a full OS, kernel and page cache (roughly 0.5-1 GB idle each). |
| Separate Vagrant file for K8s nodes | Several full-OS nodes, each running kubelet + runtime. The biggest single cost. |
| Jira in a container | Atlassian recommends about 2 GiB for the app server alone; real Jira is a heavy JVM app. |
| No coordination | Three independent stacks can't see each other, so nothing decides who gets stopped when RAM runs out. |
| Need macOS testing | A macOS VM wants 4-8 GB and cannot legitimately run on this hardware (see [macOS testing](07-macos.md)). |

**Core insight:** most of what the app talks to is an **API surface** (Slurm, Redfish, BCM, Jira, Prometheus), not a real OS. Simulate the API, keep real software only where fidelity matters, and put a governor outside everything that can freeze or stop things before the host starts swapping.

## 2. Design principles

1. **Containers by default, VMs by exception.** Containers share one kernel and one base image (shared page cache). Keep exactly one real VM for kernel/systemd/hardware-level fidelity.
2. **Simulate APIs, not machines.** One process can emulate dozens of BMCs or GPU exporters; don't run one container per BMC.
3. **Declarative topology.** One `sim.yaml` generates compose files, `slurm.conf`, Prometheus targets and BMC inventories. Scaling nodes = changing a number.
4. **Profiles with a memory contract.** Each profile declares its budget and is refused at `up` time if the host can't afford it.
5. **Observe and control from outside the failure domain.** The governor runs on the host, in its own protected cgroup, and never depends on anything it might stop.
6. **Degrade in a defined order.** Priority classes plus an escalation ladder (throttle, freeze, reclaim, stop), with hysteresis so it doesn't flap.
7. **Everything injectable.** Faults and workloads are named scenarios, triggered by CLI, Web UI button or schedule.
8. **No real disk or network hardware.** Storage is faked with metrics and tmpfs; network faults use kernel `tc netem`, not extra proxies.
9. **Time is a parameter.** A `time_dilation` multiplier compresses hours of cluster activity into minutes.
