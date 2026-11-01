# check: GPU series seen against the node's GRES count
# Contract: print one line per problem (nothing when healthy). Sourced in a subshell with $node, $mine (this node's
# exporter series), $expected (GPUs), $expected_ib (IB ports), $state (a scratch file) and the params as $P_<key>.
[ "$expected" -gt 0 ] 2>/dev/null || return 0
seen=$(printf '%s\n' "$mine" | grep -c '^DCGM_FI_DEV_GPU_TEMP{')
[ "$seen" -gt 0 ] && [ "$seen" -lt "$expected" ] && add "GPU count $seen of $expected"
