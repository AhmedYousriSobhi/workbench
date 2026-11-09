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
exec /usr/local/dcgm/dcgm-exporter-entrypoint.sh "$@"
