# Power save and reboot through the BMC farm

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

## Not exercised yet

The live test `tests/real/test_power_real.py` is written and has not been run (`./scripts/verify.sh --unit power`). Open until it has: whether slurmctld accepts `scontrol power down` with `SuspendTime` unset, how `POWERED_DOWN` prints in `sinfo -o %T`, whether a node whose slurmd keeps running while powered down registers again on `power up` or only after `ResumeTimeout`, and how slurmctld decides a reboot has finished when the container shares the host's boot time. Not run against the monitoring client. After the first live run, write each difference here with the log lines that show it.
