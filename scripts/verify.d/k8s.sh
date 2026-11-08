#!/usr/bin/env bash
# Reset the Kubernetes part's state for a clean `verify.sh --unit k8s --clean`: the etcd data (the cluster's objects) and the
# PKI, tokens and kubeconfigs. The next start makes a new cluster and new credentials.
set -eu
cd "$(dirname "$0")/../.."
rm -rf compose/generated/k8s compose/generated/secrets/k8s
