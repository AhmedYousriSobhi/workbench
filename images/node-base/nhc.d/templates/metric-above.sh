# check: a metric rises above a limit
# params: metric max
v=$(printf '%s\n' "$mine" | grep "^${P_metric}{" | awk -v m="$P_max" '$NF + 0 > m + 0 {print $NF; exit}')
[ -n "$v" ] && add "$P_metric $v above $P_max"
return 0
