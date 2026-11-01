# check: a metric falls below a limit
# params: metric min
v=$(printf '%s\n' "$mine" | grep "^${P_metric}{" | awk -v m="$P_min" '$NF + 0 < m + 0 {print $NF; exit}')
[ -n "$v" ] && add "$P_metric $v below $P_min"
return 0
