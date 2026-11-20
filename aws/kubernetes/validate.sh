#!/usr/bin/env bash
# Offline validation of the Kubernetes bootstrap, no AWS needed (Docker + kind + helm + kubectl):
#  1. pinned chart versions exist (same versions as bootstrap.sh)
#  2. platform.yaml and app.yaml pass a server-side dry run against the real CRD schemas
#  3. Helm flags used by bootstrap.sh really land in the rendered charts
# Creates a throwaway kind cluster with an isolated kubeconfig and deletes it at the end (~1.5 GB).
set -euo pipefail
cd "$(dirname "$0")"
WORK=$(mktemp -d)
trap 'kind delete cluster --name micro-test-validate --kubeconfig "$WORK/kubeconfig" >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
export KUBECONFIG="$WORK/kubeconfig" HELM_CACHE_HOME="$WORK/helm" HELM_CONFIG_HOME="$WORK/helm" HELM_DATA_HOME="$WORK/helm"

# Same pins as bootstrap.sh (single source of truth).
eval "$(grep -E '^(LBC_CHART|KARPENTER|ISTIO|KEDA)_VERSION=' bootstrap.sh)"
echo "Versions: LBC=$LBC_CHART_VERSION Karpenter=$KARPENTER_VERSION Istio=$ISTIO_VERSION KEDA=$KEDA_VERSION"

helm repo add eks https://aws.github.io/eks-charts >/dev/null
helm repo add istio https://istio-release.storage.googleapis.com/charts >/dev/null
helm repo add kedacore https://kedacore.github.io/charts >/dev/null
helm repo update >/dev/null
crds() { helm template x "$@" --include-crds |
  python3 -c 'import sys; print("\n---".join(d for d in sys.stdin.read().split("\n---") if "kind: CustomResourceDefinition" in d))'; }

kind create cluster --name micro-test-validate --kubeconfig "$KUBECONFIG" --wait 120s >/dev/null
{ crds eks/aws-load-balancer-controller --version "$LBC_CHART_VERSION" --set clusterName=x
  echo "---"; crds oci://public.ecr.aws/karpenter/karpenter --version "$KARPENTER_VERSION" --set settings.clusterName=x
  echo "---"; crds istio/base --version "$ISTIO_VERSION"
  echo "---"; crds kedacore/keda --version "$KEDA_VERSION"; } | kubectl apply --server-side -f - >/dev/null
kubectl wait --for=condition=Established crd --all --timeout=60s >/dev/null
for ns in istio-system istio-ingress micro keda; do kubectl create namespace "$ns" >/dev/null; done

# Representative values; only the shape matters for validation.
export NODE_ROLE_NAME=micro-test-node-role CLUSTER_SG_ID=sg-0123456789abcdef0 APP_SG_ID=sg-0fedcba9876543210 \
  WORKLOAD_SUBNET_ID=subnet-0123456789abcdef0 AWS_REGION=us-east-1 KARPENTER_CPU_LIMIT=16 \
  TARGET_GROUP_ARN=arn:aws:elasticloadbalancing:us-east-1:111122223333:targetgroup/micro-test-istio/0123456789abcdef \
  QUEUE_URL=https://sqs.us-east-1.amazonaws.com/111122223333/micro-test-purchases \
  DOCUMENTS_BUCKET=micro-test-documents IMAGE=111122223333.dkr.ecr.us-east-1.amazonaws.com/micro:test \
  API_MAX_REPLICAS=1 API_MESSAGES_PER_POD=8 MOCKS_FAILURE_PROBABILITY=0.02 MOCKS_LATENCY_MS=0 WORKFLOW_KEY=dummy
render() { python3 -c 'import os,string,sys; sys.stdout.write(string.Template(sys.stdin.read()).substitute(os.environ))' < "$1"; }
for f in platform.yaml app.yaml; do echo "== $f"; render "$f" | kubectl apply --dry-run=server -f - | grep -v '^Warning'; done

echo "== Helm values used by bootstrap.sh"
gateway=$(helm template istio-ingressgateway istio/gateway -n istio-ingress --version "$ISTIO_VERSION" \
  --set nodeSelector.role=base --set service.type=ClusterIP \
  --set 'service.ports[0].name=http2' --set 'service.ports[0].port=80' --set 'service.ports[0].targetPort=8080')
grep -q 'targetPort: 8080' <<<"$gateway" && grep -q 'istio: ingressgateway' <<<"$gateway" && echo "gateway: port 80->8080, label istio=ingressgateway"
helm template k oci://public.ecr.aws/karpenter/karpenter --version "$KARPENTER_VERSION" \
  --set settings.clusterName=micro-test --set nodeSelector.role=base | grep -q 'role: base' && echo "karpenter: nodeSelector role=base"
helm template l eks/aws-load-balancer-controller --version "$LBC_CHART_VERSION" --set clusterName=micro-test \
  --set enableServiceMutatorWebhook=false | grep -q 'mservice.elbv2' && { echo "LBC service webhook still enabled" >&2; exit 1; } || echo "LBC: service mutator webhook disabled"
echo "VALIDATION OK"
