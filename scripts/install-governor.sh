#!/usr/bin/env bash
# Install the governor as a standing systemd service (runs at boot, restarts on failure).
#   sudo ./scripts/install-governor.sh              install/refresh and start it
#   sudo ./scripts/install-governor.sh --uninstall  stop and remove it
#   ./scripts/install-governor.sh --print           show the unit that would be installed (no root)
# The unit points at this checkout (--workdir), so scale/set-profile work. The Grafana token is read
# from compose/generated/secrets/grafana_token at start; restart the service after a fresh `benchctl up`
# created a new token: sudo systemctl restart hpcsim-governor
set -eu
repo="$(cd "$(dirname "$0")/.." && pwd)"
unit=/etc/systemd/system/hpcsim-governor.service
slice=/etc/systemd/system/hpcsim-governor.slice
gid="$(getent group docker | cut -d: -f3 || true)"
# Prometheus (a container) scrapes the host through host-gateway = the docker bridge address
bridge="$(ip -4 -o addr show docker0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"

render() {  # the repo's unit with this checkout's path, Grafana wiring and socket group
  sed -e "s|^WorkingDirectory=.*|WorkingDirectory=$repo|" \
      -e "s|^ExecStart=.*|ExecStart=/bin/sh -c 'HPCSIM_GRAFANA_TOKEN=\$\$(cat $repo/compose/generated/secrets/grafana_token 2>/dev/null) exec /usr/bin/python3 -m governor --socket /run/hpcsim/gov.sock --require-lock --workdir $repo --grafana-url http://127.0.0.1:3000${gid:+ --socket-gid $gid}${bridge:+ --metrics-addr $bridge:9401}'|" \
      "$repo/systemd/hpcsim-governor.service"
}

case "${1:-}" in
  --print) render; exit 0 ;;
  --uninstall)
    [ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
    systemctl disable --now hpcsim-governor 2>/dev/null || true
    rm -f "$unit" "$slice"; systemctl daemon-reload; echo "governor service removed"; exit 0 ;;
  "") ;;
  *) echo "usage: $0 [--print|--uninstall]" >&2; exit 2 ;;
esac
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
install -m 0644 "$repo/systemd/hpcsim-governor.slice" "$slice"
render > "$unit"; chmod 0644 "$unit"
systemd-analyze verify "$unit" 2>&1 | grep -v "^$" || true
systemctl daemon-reload
systemctl enable hpcsim-governor >/dev/null 2>&1
systemctl restart hpcsim-governor
sleep 2
systemctl is-active --quiet hpcsim-governor && echo "governor is running; try: python -m simctl.cli status" \
  || { journalctl -u hpcsim-governor -n 20 --no-pager; exit 1; }
