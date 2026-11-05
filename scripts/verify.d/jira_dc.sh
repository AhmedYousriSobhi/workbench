#!/usr/bin/env bash
# Reset the real Jira for a clean `verify.sh --unit jira_dc --clean`: remove its containers and data volumes, so the next
# start is a fresh Jira with a fresh 3-hour licence (the tokens go, the passwords stay). The mock is untouched.
set -eu
cd "$(dirname "$0")/../.."
./scripts/benchctl remove jira-real-state -y
