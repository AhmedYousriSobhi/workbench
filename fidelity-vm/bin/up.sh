#!/usr/bin/env bash
# Bring the fidelity VM up. Refuses without ~3 GB free memory (the VM is capped at 2 GB).
# vagrant-libvirt's IP detection fails intermittently even when the VM boots fine (found in
# the earlier test lab; root cause unknown), so retry up to MAX_TRIES, destroying the domain between
# attempts.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export VAGRANT_DEFAULT_PROVIDER=libvirt

avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if [ "$avail" -lt 3000 ]; then
  echo "Only ${avail} MB available; need ~3000. Stop something first." >&2
  exit 1
fi

MAX_TRIES=${MAX_TRIES:-4}
tries=0
until timeout 900 vagrant up fidelity --provider libvirt; do
  tries=$((tries + 1))
  if [ "$tries" -ge "$MAX_TRIES" ]; then
    echo "fidelity failed to come up after $MAX_TRIES attempts (known intermittent IP detection)." >&2
    exit 1
  fi
  echo "==> attempt $tries/$MAX_TRIES failed, destroying and retrying..." >&2
  vagrant destroy -f fidelity
done

IP=$(vagrant ssh fidelity -c "hostname -I | awk '{print \$1}'" | tr -d '\r\n')
echo "fidelity is up at $IP (ssh user vagrant, key: $(pwd)/.vagrant/machines/fidelity/libvirt/private_key)"
