#!/usr/bin/env bash
# Reset hook for `verify.sh --unit dcgm --clean`: remove the exporter's override file so the run starts from an idle mock.
rm -f compose/generated/dcgm/overrides.yaml
