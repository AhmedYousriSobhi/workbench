# check: a GPU reports a non-zero XID error
[ "$expected" -gt 0 ] 2>/dev/null || return 0
xid=$(printf '%s\n' "$mine" | grep '^DCGM_FI_DEV_XID_ERRORS{' | awk '$NF != 0 {print $NF; exit}')
[ -n "$xid" ] && add "GPU XID $xid"
