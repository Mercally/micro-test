#!/usr/bin/env bash
# Resume a lab paused with aws/lab-pause.sh (~15 min): RDS, base nodes, app pods, then the consumer and
# the public intake last, so traffic is only released once the API can take it.
# Public throttling restored to THROTTLE_RATE / THROTTLE_BURST (default 10 rps / 20).
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_REGION="${AWS_REGION:-us-east-1}" AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}"
KUBECONFIG="$(mktemp -d)/config"; export KUBECONFIG
trap 'rm -rf "$(dirname "$KUBECONFIG")"' EXIT
step() { printf '\n== %s\n' "$*"; }

step "RDS start (~5–10 min)"
status=$(aws rds describe-db-instances --db-instance-identifier micro-test-db --query "DBInstances[0].DBInstanceStatus" --output text)
[ "$status" = stopped ] && aws rds start-db-instance --db-instance-identifier micro-test-db >/dev/null
aws rds wait db-instance-available --db-instance-identifier micro-test-db
echo "  RDS available"

step "Base node group back to 2 (stack 05 NodeCount=2, ~5 min)"
aws cloudformation deploy --stack-name micro-test-eks --template-file aws/cloudformation/05-eks.yaml \
  --capabilities CAPABILITY_IAM --parameter-overrides NodeCount=2 --no-fail-on-empty-changeset

step "App back (KEDA resumes api; worker and mocks to 1). Karpenter launches the workload node"
aws eks update-kubeconfig --name micro-test >/dev/null
kubectl get ns micro >/dev/null || { echo "kubectl has no access: is your IP in AdminCidr (stack 05)?" >&2; exit 1; }
kubectl wait --for=condition=Ready nodes -l role=base --timeout=300s
kubectl -n micro annotate scaledobject api autoscaling.keda.sh/paused-replicas- || true
kubectl -n micro scale deploy/worker deploy/mocks --replicas=1
for d in api worker mocks; do kubectl -n micro rollout status "deploy/$d" --timeout=600s; done

step "Consumer on: queued messages start flowing"
aws cloudformation deploy --stack-name micro-test-messaging --template-file aws/cloudformation/04-messaging.yaml \
  --capabilities CAPABILITY_IAM --parameter-overrides ConsumerEnabled=true --no-fail-on-empty-changeset

step "Public intake open: ${THROTTLE_RATE:-10} rps, burst ${THROTTLE_BURST:-20}"
aws cloudformation deploy --stack-name micro-test-ingress --template-file aws/cloudformation/06-ingress.yaml \
  --parameter-overrides ThrottlingRateLimit="${THROTTLE_RATE:-10}" ThrottlingBurstLimit="${THROTTLE_BURST:-20}" \
  --no-fail-on-empty-changeset
kubectl get nodes -L role,karpenter.sh/nodepool,node.kubernetes.io/instance-type
echo "Lab resumed."
