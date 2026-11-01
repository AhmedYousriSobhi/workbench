#!/usr/bin/env bash
# Apply the k8s fixtures to the current kubectl context. Idempotent.
# Usage: apply.sh [REAL_NODE]   (default: first node)
# Fake GPUs: nvidia.com/gpu is patched onto the node status (Kubernetes' documented
# extended-resource technique), so pods can request GPUs with no hardware.
set -euo pipefail
cd "$(dirname "$0")"
NODE="${1:-$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')}"

kubectl patch node "$NODE" --subresource=status --type=json -p \
  '[{"op":"add","path":"/status/capacity/nvidia.com~1gpu","value":"4"},{"op":"add","path":"/status/allocatable/nvidia.com~1gpu","value":"4"}]' >/dev/null
kubectl label node "$NODE" \
  nvidia.com/gpu.present=true nvidia.com/gpu.product=NVIDIA-A100-SXM4-80GB \
  nvidia.com/gpu.count=4 nvidia.com/mig.strategy=single nvidia.com/cuda.driver.major=550 \
  --overwrite >/dev/null

kubectl apply -f 00-namespaces.yaml
kubectl apply -f .
kubectl -n team-a rollout restart deploy/web >/dev/null

# Kubeconfig for the limited user, written to $VIEWER_KUBECONFIG if set.
if [ -n "${VIEWER_KUBECONFIG:-}" ]; then
  TOKEN=$(kubectl create token viewer -n team-a --duration=720h)
  SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
  cat > "$VIEWER_KUBECONFIG" <<Y
apiVersion: v1
kind: Config
clusters: [{name: c, cluster: {server: "$SERVER", insecure-skip-tls-verify: true}}]
users: [{name: viewer, user: {token: "$TOKEN"}}]
contexts: [{name: x, context: {cluster: c, user: viewer, namespace: team-a}}]
current-context: x
Y
  chmod 600 "$VIEWER_KUBECONFIG"
fi
echo fixtures-ok
