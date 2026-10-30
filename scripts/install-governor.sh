#!/usr/bin/env bash
# Install the governor as a standing systemd service (runs at boot, restarts on failure).
#   sudo ./scripts/install-governor.sh              install/refresh and start it
#   sudo ./scripts/install-governor.sh --uninstall  stop and remove it
#   ./scripts/install-governor.sh --print           show the unit that would be installed (no root)
# The unit points at this checkout (--workdir), so scale/set-profile work. The Grafana token is read
# from compose/generated/secrets/grafana_token at start; restart the service after a fresh `benchctl up`
# created a new token: sudo systemctl restart workbench-governor
set -eu
repo="$(cd "$(dirname "$0")/.." && pwd)"
unit=/etc/systemd/system/workbench-governor.service
slice=/etc/systemd/system/workbench-governor.slice
gid="$(getent group docker | cut -d: -f3 || true)"
# Prometheus (a container) scrapes the host through host-gateway = the docker bridge address
bridge="$(ip -4 -o addr show docker0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"

old_units() {  # #111: this service was called hpcsim-governor; remove the old units so only one governor runs
  if [ -f /etc/systemd/system/hpcsim-governor.service ] || [ -f /etc/systemd/system/hpcsim-governor.slice ]; then
    systemctl disable --now hpcsim-governor 2>/dev/null || true
    rm -f /etc/systemd/system/hpcsim-governor.service /etc/systemd/system/hpcsim-governor.slice
    systemctl daemon-reload; echo "removed the old hpcsim-governor units"
  fi
}

render() {  # the repo's unit with this checkout's path, Grafana wiring and socket group
  sed -e "s|^WorkingDirectory=.*|WorkingDirectory=$repo|" \
      -e "s|^ExecStart=.*|ExecStart=/bin/sh -c 'WORKBENCH_GRAFANA_TOKEN=\$\$(cat $repo/compose/generated/secrets/grafana_token 2>/dev/null) exec /usr/bin/python3 -m governor --socket /run/workbench/gov.sock --require-lock --workdir $repo --grafana-url http://127.0.0.1:3000${gid:+ --socket-gid $gid}${bridge:+ --metrics-addr $bridge:9401}'|" \
      "$repo/systemd/workbench-governor.service"
}

case "${1:-}" in
  --print) render; exit 0 ;;
  --uninstall)
    [ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
    old_units
    systemctl disable --now workbench-governor 2>/dev/null || true
    rm -f "$unit" "$slice"; systemctl daemon-reload; echo "governor service removed"; exit 0 ;;
  "") ;;
  *) echo "usage: $0 [--print|--uninstall]" >&2; exit 2 ;;
esac
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
old_units
install -m 0644 "$repo/systemd/workbench-governor.slice" "$slice"
render > "$unit"; chmod 0644 "$unit"
systemd-analyze verify "$unit" 2>&1 | grep -v "^$" || true
systemctl daemon-reload
systemctl enable workbench-governor >/dev/null 2>&1
systemctl restart workbench-governor
sleep 2
systemctl is-active --quiet workbench-governor && echo "governor is running; try: python -m benchctl.cli status" \
  || { journalctl -u workbench-governor -n 20 --no-pager; exit 1; }
