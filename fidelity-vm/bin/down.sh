#!/usr/bin/env bash
# down.sh          destroy the VM and its disk
# down.sh --save   managedsave: write RAM to disk and free it, resumable with up.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [ "${1:-}" = "--save" ]; then
  dom=$(virsh -c qemu:///system list --name | grep -m1 fidelity) || { echo "fidelity is not running" >&2; exit 1; }
  virsh -c qemu:///system managedsave "$dom"
else
  vagrant destroy -f fidelity
fi
