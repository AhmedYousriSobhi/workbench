#!/usr/bin/env bash
# Reset hook for `verify.sh --unit lab --clean`: remove the labs the live test imports (lab-real-*) and any leftover one-shot of a
# validation. Labs you imported yourself are never touched.
set -u
cd "$(dirname "$0")/../.."
rm -rf compose/generated/lab/lab-real-*
docker ps -aq --filter "name=lab-validate-" | xargs -r docker rm -f >/dev/null 2>&1 || true
