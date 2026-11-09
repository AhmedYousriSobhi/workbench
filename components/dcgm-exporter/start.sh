#!/bin/sh
# The exporter's entrypoint for two parts (issues 426, 428):
#  - `dcgm`: MOCK_NVML_PROFILE names a mock profile; keep its first MOCK_NVML_GPUS devices (the mock reads the profile's device list,
#    MOCK_NVML_NUM_DEVICES only applies without a profile).
#  - `real-gpu`: no profile; the host's driver library comes in with the CDI device, so the mock library must not be found first.
set -eu
if [ -z "${MOCK_NVML_PROFILE:-}" ]; then
  unset LD_LIBRARY_PATH
else
  awk -v N="${MOCK_NVML_GPUS:-8}" '
    /^devices:/ { inside = 1; print; next }
    inside && /^[a-z_]+:/ { inside = 0 }
    inside && /^  - index:/ { n++ }
    !inside || n <= N { print }' "$MOCK_NVML_PROFILE" > /tmp/mock-nvml.yaml
  # a card of another model than the profile's (an H200 on the H100 profile), and GPU UUIDs that differ from node to node (issue 435)
  if [ -n "${MOCK_NVML_NAME:-}" ]; then sed -i "s|^\( *name:\) \".*\"|\1 \"$MOCK_NVML_NAME\"|" /tmp/mock-nvml.yaml; fi
  if [ -n "${MOCK_NVML_NODE_INDEX:-}" ]; then sed -i "s|GPU-01000100-|$(printf 'GPU-%08x-' $((0x01000100 + MOCK_NVML_NODE_INDEX * 4096)))|" /tmp/mock-nvml.yaml; fi
  export MOCK_NVML_CONFIG=/tmp/mock-nvml.yaml
fi
# One host engine on 127.0.0.1:5555 that the exporter reads from, so `dcgmi` in this container sees the same GPUs (embedded mode listens nowhere).
# module 9 is SysMon (CPU metrics): it cannot load on a non-NVIDIA CPU and, with the real driver, took the host engine down
nv-hostengine -n -b 127.0.0.1 --denylist-modules 9 -f /tmp/nv-hostengine.log > /dev/null 2>&1 &  # not daemonised: a forked daemon vanished under the real driver
i=0; until dcgmi discovery -l > /dev/null 2>&1; do i=$((i + 1)); [ "$i" -gt 30 ] && { echo "nv-hostengine did not start" >&2; cat /tmp/nv-hostengine.log >&2; exit 1; }; sleep 1; done
# The exporter labels series with the host it connects to, so connect by this container's own name (mapped to 127.0.0.1 first), not `localhost`.
me=$(hostname)
{ echo "127.0.0.1 $me"; cat /etc/hosts; } > /tmp/hosts.new && cat /tmp/hosts.new > /etc/hosts
export DCGM_REMOTE_HOSTENGINE_INFO="$me:5555"
exec /usr/local/dcgm/dcgm-exporter-entrypoint.sh "$@"
