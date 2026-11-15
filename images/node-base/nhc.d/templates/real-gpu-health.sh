# check: the node's real NVIDIA card is missing, reports an XID error or is hotter than its own slowdown temperature
# Only a node that has `nvidia-smi` (the real card's CDI device, issue 428) runs it; every simulated node has none and skips it.
command -v nvidia-smi > /dev/null || return 0
if ! timeout 5 nvidia-smi -L 2> /dev/null | grep -q '^GPU '; then add "real GPU missing"; return 0; fi
xid=$(printf '%s\n' "$mine" | grep '^DCGM_FI_DEV_XID_ERRORS{' | awk '$NF != 0 {print $NF; exit}')
[ -n "$xid" ] && add "real GPU XID $xid"
temp=$(printf '%s\n' "$mine" | grep '^DCGM_FI_DEV_GPU_TEMP{' | awk '$NF + 0 > m {m = $NF + 0} END {if (NR) print m}')
slow=$(timeout 5 nvidia-smi -q -d TEMPERATURE 2> /dev/null | awk -F: '/GPU Slowdown Temp/ {gsub(/[^0-9]/, "", $2); print $2; exit}')
[ -n "$temp" ] && [ -n "$slow" ] && [ "$temp" -gt "$slow" ] 2> /dev/null && add "real GPU ${temp}C over its slowdown ${slow}C"
return 0
