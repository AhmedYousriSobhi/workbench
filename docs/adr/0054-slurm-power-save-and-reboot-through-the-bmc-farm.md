# ADR 0054: Slurm power save and reboot through the BMC farm

Status: accepted (design of epic 557, A-8 and A-9). Issue 545.

## Decision

- Slurm's own mechanisms, no new process: `SuspendProgram`/`ResumeProgram` are one script, `images/slurm-ctl/power.sh`, installed as `power-off` and `power-on` (slurmctld passes only a hostlist, so the name says which way); `RebootProgram` is `images/node-base/reboot.sh`, which runs on the node.
- The BMC farm is the source of truth for power: the scripts post a Redfish `Reset` (`ForceOff`, `On`) to the farm's plain port. A failure there fails the program, as a real power program's failure would. The BCM mock and the GPU exporter are told the same way a BMC power change does in a scenario, best effort, so every view agrees.
- Reboot is a power cycle (`ForceOff`, pause, `On`) plus the `slurmd` hold file that `slurmd-run` already waits on; the container stays up. The work runs detached because it kills `slurmd`, its own parent.
- `SuspendTime` is not written: the default is off in every Slurm version, so nothing idles into power save, and the value `NONE` in the design could not be checked against 26.05.5 offline (a wrong value stops slurmctld).
- Scenario keys `power_down`, `power_up`, `reboot` on target `slurm`, separate from `power_off`/`power_on` (a node killed behind Slurm's back). They need Docker, so the page skips them.

## Consequences

A Slurm power down does not stop the node container or its `slurmd` (slurmctld has no Docker access): the node is simply not scheduled or pinged while `POWERED_DOWN`. Whether it registers again on `power up` without a restart, and how a reboot is seen to finish when the container shares the host's boot time, are not known until the live test runs (`docs/guides/power-and-reboot.md`). If either fails, the fix belongs in `reboot.sh` or a node-side step, not in slurmctld.
