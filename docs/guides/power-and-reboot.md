<p align="center"><img src="../assets/art/guide-power-and-reboot-hero.svg" alt="A node going dark and coming back, joined by wires that show power flowing" width="720"></p>

# Power save and reboot through the BMC farm

**In 30 seconds:** use Slurm's own power save and reboot commands and the fake BMCs follow: `sinfo` shows `POWERED_DOWN`, Redfish shows `PowerState` Off, and the node returns to service when you power it up.

## Do it

```bash
docker exec workbench-slurmctld-1 scontrol power down cn002
docker exec workbench-slurmctld-1 scontrol power up cn002
```

Slurm's own power states and reboot workflow, wired to the fake BMCs (issue 545, ADR 0054). A client sees what it would on a real cluster: `POWERED_DOWN` and `POWERING_UP` in `sinfo`, a Redfish `PowerState` of `Off`, and a node that reboots and returns to service.

## Power a node down and up

```bash
docker exec workbench-slurmctld-1 scontrol power down cn002     # slurmctld runs SuspendProgram (images/slurm-ctl/power.sh)
docker exec workbench-slurmctld-1 sinfo -N -n cn002 -o "%N %T"  # POWERED_DOWN (idle~ in the short form)
curl -s http://127.0.0.1:8000/cn002/redfish/v1/Systems/1 | python3 -m json.tool | grep PowerState   # "Off"
docker exec workbench-slurmctld-1 scontrol power up cn002       # ResumeProgram: BMC On, then POWERING_UP until slurmd registers
```

`power.sh` resets the node's BMC (`ForceOff`, `On`) and also tells the BCM mock (`DOWN`, `UP`) and the GPU exporter (the node stops publishing, then publishes again), so every view of the node agrees. If the BMC farm refuses, the program exits 1 and slurmctld logs the failure; a BCM mock or exporter that is not running is skipped. `SuspendTime` is left at its default, so nothing powers down by itself: only `scontrol power down` does.

## Reboot

```bash
docker exec workbench-slurmctld-1 scontrol reboot ASAP nextstate=resume reason="kernel update" cn003
```

`ASAP` drains the node, waits for its jobs to end, then runs `RebootProgram` on the node (`images/node-base/reboot.sh`): the BMC gets `ForceOff`, slurmd is held down with the hold file `slurmd-run` already uses, the BMC gets `On` about 10 s later (`REBOOT_DOWN_SECONDS` in the node's environment changes that) and the hold file goes. The node's Redfish log shows `Reset ForceOff` and `Reset On`; with `nextstate=resume` the node returns to service when slurmd registers. The container itself stays up.

## In a scenario

`target: slurm` takes `power_down: [nodes]`, `power_up: [nodes]` and `reboot: [nodes]` (with an optional `reason`). They are not the existing `power_off` / `power_on`, which kill and revive a node behind Slurm's back as a power cut would. The scenario `reboot-asap` (SC-36) reboots `cn002` and powers `cn003` down for 50 s:

```bash
./scripts/benchctl scenario run reboot-asap
```

These steps need the host CLI; the page skips them.

## How it fits with the rest

- **Health check (C-15):** it runs on a node that answers, so a powered-down node is not checked; it is checked again once it returns. A node the check drained stays drained through a reboot unless `nextstate=resume` is given.
- **Jira:** a Slurm power down is an administrator's act, so no ticket is filed (a BMC power-off in a scenario, `power: Off`, still files one).
- **Existing power-off (issues 136, 151, 154):** unchanged; `ReturnToService=1` brings a node back when slurmd answers.

## What the live run showed (2026-11-12, `./scripts/verify.sh --unit power`: 3 passed)

Three things differ from what the Slurm manual suggests, and the images handle them:

- **A container shares the host's uptime.** Slurm decides a node has booted (and so left `POWERING_UP` or `REBOOT_ISSUED`) from the uptime in its registration, so a restarted slurmd was never believed: the node ended `down~` after `ResumeTimeout`. `slurmd-run` now starts slurmd with `-b` ("just booted") after a power up or a reboot.
- **No slurmd on a powered-down node.** `slurmd-run` follows Slurm's own state (`scontrol show node`): `POWERED_DOWN` or `POWERING_DOWN` holds slurmd down, `POWERING_UP` lets it start. Without that the slurmd of a "powered down" node kept running and never registered again.
- **`RebootProgram` runs in the foreground.** slurmd waits for it and Slurm ends anything the program leaves behind, so a detached copy never ran. `reboot.sh` does the whole cycle itself, and slurmd is killed in the middle of it.

Still not exercised: a node with a running job through `scontrol reboot ASAP` (the node should drain first), the monitoring client.

## Under the hood

<p align="center"><img src="../assets/art/guide-power-and-reboot-diagram.svg" alt="One power down command runs one script that tells the BMC, the BCM mock and the GPU exporter" width="720"></p>

> [!HOOD]
> No new process: `SuspendProgram` and `ResumeProgram` are one script, `images/slurm-ctl/power.sh`, installed under two names (slurmctld passes only a hostlist, so the name says which way). It posts a Redfish `Reset` to the BMC farm, which is the source of truth for power, then tells the BCM mock and the GPU exporter so every view agrees. A BMC farm failure fails the program, as a real one would.
> A reboot is a power cycle (`ForceOff`, a pause, `On`) plus the hold file that `slurmd-run` waits on; the container stays up. `SuspendTime` is not written, so nothing powers down by itself.

Source: `images/slurm-ctl/power.sh`, `images/node-base/reboot.sh`, `images/node-base/slurmd-run`, [ADR 0054](../adr/0054-slurm-power-save-and-reboot-through-the-bmc-farm.md)

## Tips

> [!TIP]
> `power_down` is not `power_off`. The first goes through Slurm like an administrator's act; the second kills a node behind Slurm's back, like a power cut.

## If it goes wrong

| Symptom | Try |
|---|---|
| The node ends `down~` after a power up | the image is older than `slurmd-run -b`; rebuild with `benchctl up` |
| The program exits 1 | the BMC farm refused; check `docker logs workbench-bmc-farm-1` |
| A scenario step is skipped on the page | these steps need the host CLI; run `benchctl scenario run NAME` |

## Learn more

- [Scenarios](../manual/06-scenarios.md)
- [Slurm resilience](slurm-resilience.md)
- [Client testing](client-testing.md)
