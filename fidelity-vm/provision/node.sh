#!/usr/bin/env bash
# Real node_exporter so the app can read genuine kernel counters from one node.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
cloud-init status --wait >/dev/null 2>&1 || true
apt-get update -qq
apt-get install -y -qq prometheus-node-exporter
systemctl enable --now prometheus-node-exporter
