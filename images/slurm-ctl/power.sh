#!/bin/sh
# Slurm power saving through the BMC farm (issue 545, config lab A-8). Installed twice, as power-off (SuspendProgram) and
# power-on (ResumeProgram); slurmctld calls it with a hostlist and the name tells which way. `scontrol power down|up` triggers it
# (SuspendTime is left at its default, so nothing idles off by itself).
# The BMC call is the one that counts: if the farm refuses, this exits 1 and slurmctld logs the failure like a real power program that failed.
# The BCM mock and the GPU exporter are told too, so every view of the node agrees (the same three a BMC power change touches in a scenario);
# a part that is not running is skipped.
mode=$(basename "$0")
case "$mode" in
  power-off) reset=ForceOff; bcm=DOWN; gone=true ;;
  power-on)  reset=On;       bcm=UP;   gone=false ;;
  *) echo "run as power-off or power-on, not $mode" >&2; exit 2 ;;
esac
post() {
  python3 -c 'import sys,urllib.request as u; u.urlopen(u.Request(sys.argv[1], sys.argv[2].encode(), method="POST"), timeout=5).read()' "$1" "$2" >/dev/null 2>&1
}
rc=0
for n in $(scontrol show hostnames "$1"); do
  if post "http://bmc-farm:8000/$n/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" "{\"ResetType\":\"$reset\"}"; then
    echo "$mode $n: BMC $reset"
  else
    echo "$mode $n: the BMC farm refused ResetType $reset" >&2; rc=1
  fi
  post "http://bcm-mock:8081/sim/inject" "{\"node\":\"$n\",\"state\":\"$bcm\"}"
  post "http://gpu-exporter-farm:9400/sim/inject" "{\"node\":\"$n\",\"down\":$gone}"
done
exit $rc
