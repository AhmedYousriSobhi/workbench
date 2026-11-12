#!/bin/sh
# RebootProgram (issue 545, config lab A-9): `scontrol reboot` makes slurmd run this instead of rebooting a host. The node power-cycles
# through the BMC farm (Off, a pause, On, so its Redfish log shows both resets), slurmd is held down and started again with the same hold
# file the resilience scenarios use (slurmd-run), and the BCM mock and the GPU exporter follow. The container itself stays up.
HOLD=/run/workbench/slurmd.hold
BOOTED=/run/workbench/slurmd.booted
node=$(hostname)
post() {
  python3 -c 'import sys,urllib.request as u; u.urlopen(u.Request(sys.argv[1], sys.argv[2].encode(), method="POST"), timeout=5).read()' "$1" "$2" >/dev/null 2>&1
}
# slurmd runs this and waits for it, and Slurm ends any child that outlives the program (a detached copy never got to run), so the whole
# cycle runs here, in the foreground. slurmd is killed in the middle of it; this script is not its child's child, it carries on.
exec >/dev/null 2>&1
post "http://bmc-farm:8000/$node/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" '{"ResetType":"ForceOff"}'
post "http://bcm-mock:8081/sim/inject" "{\"node\":\"$node\",\"state\":\"DOWN\"}"
post "http://gpu-exporter-farm:9400/sim/inject" "{\"node\":\"$node\",\"down\":true}"
touch "$HOLD"; pkill -x slurmd
sleep "${REBOOT_DOWN_SECONDS:-10}"
post "http://bmc-farm:8000/$node/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" '{"ResetType":"On"}'
post "http://bcm-mock:8081/sim/inject" "{\"node\":\"$node\",\"state\":\"UP\"}"
post "http://gpu-exporter-farm:9400/sim/inject" "{\"node\":\"$node\",\"down\":false}"
touch "$BOOTED"; rm -f "$HOLD"  # slurmd comes back with -b (slurmd-run), so Slurm sees a reboot and honours nextstate
exit 0
