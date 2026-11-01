# check: InfiniBand ports missing or not Active (state 4)
[ "$expected_ib" -gt 0 ] 2>/dev/null || return 0
ports=$(printf '%s\n' "$mine" | grep -c '^node_infiniband_state_id{')
down=$(printf '%s\n' "$mine" | grep '^node_infiniband_state_id{' | awk '$NF != 4' | sed -n 's/.*device="\([^"]*\)".*/\1/p' | paste -sd, -)
[ "$ports" -gt 0 ] && [ "$ports" -lt "$expected_ib" ] && add "IB ports $ports of $expected_ib"
[ -n "$down" ] && add "IB port down: $down"
return 0
