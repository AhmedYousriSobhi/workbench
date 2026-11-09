#!/bin/sh
# Keep the first MOCK_NVML_GPUS devices of the profile (the mock reads the profile's device list; MOCK_NVML_NUM_DEVICES only applies
# when there is no profile), then run the exporter's own entrypoint. Issue 426.
set -eu
profile=${MOCK_NVML_PROFILE:?set MOCK_NVML_PROFILE to a profile in /opt/mocknvml/configs}
awk -v N="${MOCK_NVML_GPUS:-8}" '
  /^devices:/ { inside = 1; print; next }
  inside && /^[a-z_]+:/ { inside = 0 }
  inside && /^  - index:/ { n++ }
  !inside || n <= N { print }' "$profile" > /tmp/mock-nvml.yaml
export MOCK_NVML_CONFIG=/tmp/mock-nvml.yaml
# One host engine on 127.0.0.1:5555 that the exporter reads from, so `dcgmi` in this container sees the same GPUs (embedded mode listens nowhere).
nv-hostengine -b 127.0.0.1 -f /tmp/nv-hostengine.log > /dev/null
i=0; until dcgmi discovery -l > /dev/null 2>&1; do i=$((i + 1)); [ "$i" -gt 30 ] && { echo "nv-hostengine did not start" >&2; cat /tmp/nv-hostengine.log >&2; exit 1; }; sleep 1; done
export DCGM_REMOTE_HOSTENGINE_INFO=localhost:5555
exec /usr/local/dcgm/dcgm-exporter-entrypoint.sh "$@"
