#!/usr/bin/env bash
# Pause the AWS lab without deleting anything (resume: aws/lab-resume.sh, ~15 min).
# Stops what bills per hour and can come back as-is: public intake, consumer, app pods, Karpenter and
# base nodes, RDS.
# Keeps billing (~$4.4/day): EKS control plane, NAT + its IPv4, NLB, storage. Messages and data are kept:
# SQS holds the queue (4 days), the outbox holds unpublished sales, RDS keeps its data.
# RDS auto-starts after 7 days stopped (AWS rule): pause longer than that = run this again, or delete the lab.
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_REGION="${AWS_REGION:-us-east-1}" AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}"
KUBECONFIG="$(mktemp -d)/config"; export KUBECONFIG
trap 'rm -rf "$(dirname "$KUBECONFIG")"' EXIT
step() { printf '\n== %s\n' "$*"; }

step "Public intake closed: API Gateway answers 429 to everything (UI, load.sh, bursts)"
aws cloudformation deploy --stack-name micro-test-ingress --template-file aws/cloudformation/06-ingress.yaml \
  --parameter-overrides ThrottlingRateLimit=0 ThrottlingBurstLimit=0 --no-fail-on-empty-changeset

step "Consumer off: new messages wait in SQS instead of failing against a stopped API"
aws cloudformation deploy --stack-name micro-test-messaging --template-file aws/cloudformation/04-messaging.yaml \
  --capabilities CAPABILITY_IAM --parameter-overrides ConsumerEnabled=false --no-fail-on-empty-changeset

if [ "$(aws eks describe-nodegroup --cluster-name micro-test --nodegroup-name base --query nodegroup.scalingConfig.desiredSize --output text)" != 0 ]; then
  step "App to 0 pods (KEDA paused, worker and mocks scaled to 0)"
  aws eks update-kubeconfig --name micro-test >/dev/null
  kubectl get ns micro >/dev/null || { echo "kubectl has no access: is your IP in AdminCidr (stack 05)?" >&2; exit 1; }
  kubectl -n micro annotate scaledobject api autoscaling.keda.sh/paused-replicas=0 --overwrite
  kubectl -n micro scale deploy/worker deploy/mocks --replicas=0

  step "Waiting for Karpenter to remove its nodes (it must run until they are gone)"
  for _ in $(seq 1 40); do
    n=$(kubectl get nodes -l karpenter.sh/nodepool --no-headers 2>/dev/null | wc -l | tr -d ' ')
    [ "$n" = 0 ] && break
    echo "  karpenter nodes: $n"; sleep 15
  done
  [ "$n" = 0 ] || { echo "Karpenter nodes still present; aborting before stopping Karpenter itself" >&2; exit 1; }

  step "Base node group to 0 (stack 05 NodeCount=0, ~5 min)"
  aws cloudformation deploy --stack-name micro-test-eks --template-file aws/cloudformation/05-eks.yaml \
    --capabilities CAPABILITY_IAM --parameter-overrides NodeCount=0 --no-fail-on-empty-changeset
fi

step "RDS stop (data kept; AWS restarts it automatically after 7 days)"
status=$(aws rds describe-db-instances --db-instance-identifier micro-test-db --query "DBInstances[0].DBInstanceStatus" --output text)
if [ "$status" = available ]; then
  aws rds stop-db-instance --db-instance-identifier micro-test-db --query "DBInstance.DBInstanceStatus" --output text
else
  echo "  RDS is '$status': nothing to do"
fi

step "Running EC2 (expected: none)"
aws ec2 describe-instances --filters Name=instance-state-name,Values=pending,running \
  --query "Reservations[].Instances[].[InstanceId,InstanceType]" --output text
echo "Lab paused. Resume: aws/lab-resume.sh"
