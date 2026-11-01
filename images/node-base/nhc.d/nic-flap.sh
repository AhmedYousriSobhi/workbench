# check: the NIC link keeps changing (carrier counter rises between runs)
carrier=$(printf '%s\n' "$mine" | grep '^node_network_carrier_changes_total{' | awk '{print $NF; exit}')
[ -n "$carrier" ] || return 0
before=$(cat "$state" 2>/dev/null)
[ -n "$before" ] && [ "$carrier" -gt "$before" ] && add "NIC eth0 flapping ($((carrier - before)) link changes)"
echo "$carrier" > "$state" 2>/dev/null
return 0
