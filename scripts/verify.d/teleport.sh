#!/usr/bin/env bash
# Reset Teleport's state for a clean `verify.sh --unit teleport --clean`. An empty data directory is a new cluster with a
# new CA; benchctl/teleport.py then drops the host certificates the old CA signed and signs new ones.
set -eu
cd "$(dirname "$0")/../.."
rm -rf compose/generated/teleport/data
